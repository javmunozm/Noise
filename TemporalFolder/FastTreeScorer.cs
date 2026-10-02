using System;
using System.Collections.Generic;
using System.Linq;
using DataProcessor.Connections;
using Microsoft.ML;
using Microsoft.ML.Data;

namespace DataProcessor.Models
{
    public class FastTreeScorer : INumberScorer
    {
        public string Name => "FastTree";

        private const int MIN_NUMBER = 1;
        private const int MAX_NUMBER = 25;
        private const int WARMUP = 100;
        private const int SEED = 42;

        private MLContext? mlContext;
        private ITransformer? trainedModel;
        private List<Connections.SeriesData>? cachedHistory;

        public class FeatureRow
        {
            public float Number { get; set; }           // pass-through identifier; not used as a model feature
            // --- existing features (any-event appearances) ---
            public float Recency { get; set; }
            public float FreqLast10 { get; set; }
            public float FreqLast20 { get; set; }
            public float FreqLast50 { get; set; }
            public float FreqLast100 { get; set; }
            public float MeanGap { get; set; }
            public float GapStd { get; set; }
            public float AppearancesTotal { get; set; }
            public float Streak { get; set; }
            // --- event-1 specific + co-appearance ---
            public float Event1FreqLast10 { get; set; }
            public float Event1FreqLast20 { get; set; }
            public float Event1FreqLast50 { get; set; }
            // Event1Recency tested 2026-04-29: dropped 70.4%→66.4%.
            // Event1MeanGap/GapStd tested 2026-04-29: dropped 70.4%→68.2%.
            // Both redundant with Event1FreqLast* at this scale — add noise not signal.
            public float MeanCoAppear50 { get; set; }
            public float TopCoAppear50 { get; set; }
            // Event1FreqLast100 tested 2026-05-01 (flat-zone AUC=0.531): dropped 70.4%→67.9% (Δ −2.5%). Correlated with existing Event1FreqLast50; tree already captures this range.
            // EventSpreadLast50 tested 2026-05-01 (flat-zone AUC=0.538): dropped 70.4%→67.5% (Δ −2.9%). Cross-event variance adds noise despite AUC signal — tree can't leverage it with this label.
            // Both+combined tested 2026-05-01: dropped 70.4%→68.2% (Δ −2.2%). No synergy.
            // TopPartnersFreqLast10/TopPartnersRecency tested 2026-05-01: dropped 70.4%→67.1%. Top-3 partner activity is too unstable a signal at n=20 series.
            // TwoPassFastTreeScorer (K=8) tested 2026-05-01: dropped 70.4%→67.1% (Δ −3.2%). K-set proxy diverges from actual Pass-1 picks; set-level errors propagate. Needs Pass-1 accuracy ≥90%. See Models/TwoPassFastTreeScorer.cs.
            // MultiEventScorer (label=>=2/7 events) tested 2026-05-01: dropped 70.4%→67.9% (Δ −2.5%). Different label, but features don't separate multi-event numbers from event-1 numbers at this scale. See Models/MultiEventScorer.cs.
            // Routed ensemble (Pool when Poolconf>FTconf) tested 2026-05-01: 0% delta. Pool confidence locked at ~0.56 every series vs FT ~0.63-0.69 — routing signal absent; Pool is structurally less concentrated.
            // AvgEventsPerSeries50 tested 2026-05-01: dropped 70.4%→68.2%. Pool's edge on 3207/3209 is from the any-event label, not this feature.
            // PoolFreqLast50 tested 2026-05-01: 0% change (70.4%→70.4%). Linear rescaling of FreqLast50 — no new info for trees.
            // Hyperparameter sweep tested 2026-05-01 (all vs baseline 100 trees/lr=0.2/minLeaf=10):
            //   ConfigA (300 trees, lr=0.05, minLeaf=10): 69.6% (Δ −0.8%). Slower lr underfits at this data scale.
            //   ConfigB (100 trees, lr=0.2,  minLeaf=50): 67.5% (Δ −2.9%). Higher leaf min over-smooths 25-number discrimination.
            //   ConfigC (300 trees, lr=0.05, minLeaf=50): 68.6% (Δ −1.8%). Combined penalties, no synergy.
            // Event1VsPoolRatio tested 2026-04-30: dropped FastTree 70.4%→67.5%. Adds noise at this scale.
            // PosFreq0..6 tested 2026-04-29: dropped FastTree 70.4%→68.6%. Variable event
            // counts in historical data (1–13 events per series) make positions 2–6 noisy.
            public bool Label { get; set; }
        }

        public class PredictionRow
        {
            [Microsoft.ML.Data.ColumnName("NumberPassThrough")]
            public float Number { get; set; }   // echoed from input so we can match prob → number
            public bool PredictedLabel { get; set; }
            public float Probability { get; set; }
            public float Score { get; set; }
        }

        public void Train(IReadOnlyList<Connections.SeriesData> history)
        {
            cachedHistory = history.ToList();
            mlContext = new MLContext(seed: SEED);

            var trainRows = new List<FeatureRow>();

            for (int t = WARMUP; t < cachedHistory.Count; t++)
            {
                // prefix = strictly the series BEFORE index t (no leakage)
                var prefix = cachedHistory.Take(t).ToList();
                var s = cachedHistory[t];
                if (s.AllCombinations.Count == 0) continue;

                // Compute features once per number — they depend only on prefix, not on which
                // event we're predicting. Then emit one training row per event, each with the
                // same features but a different label. This gives 7× more training rows while
                // keeping the label discriminative (~56% true per event) and leak-free.
                for (int n = MIN_NUMBER; n <= MAX_NUMBER; n++)
                {
                    // One row per (series, number), label = event-1 only.
                    // Tested 2026-04-29: all-events 7× rows dropped FastTree 70.4%→66.8%.
                    // The event-1 specific features (Event1FreqLast*,CoAppear*) conflict with
                    // mixed-event labels — adding noise rows hurts more than the data volume helps.
                    var feats = ComputeFeatures(prefix, n);
                    feats.Label = s.AllCombinations[0].Contains(n);
                    trainRows.Add(feats);
                }
            }

            if (trainRows.Count == 0)
            {
                trainedModel = null;
                return;
            }

            var dataView = mlContext.Data.LoadFromEnumerable(trainRows);

            var pipeline = mlContext.Transforms.CopyColumns("NumberPassThrough", nameof(FeatureRow.Number))
                .Append(mlContext.Transforms.Concatenate("Features",
                    nameof(FeatureRow.Recency),
                    nameof(FeatureRow.FreqLast10),
                    nameof(FeatureRow.FreqLast20),
                    nameof(FeatureRow.FreqLast50),
                    nameof(FeatureRow.FreqLast100),
                    nameof(FeatureRow.MeanGap),
                    nameof(FeatureRow.GapStd),
                    nameof(FeatureRow.AppearancesTotal),
                    nameof(FeatureRow.Streak),
                    nameof(FeatureRow.Event1FreqLast10),
                    nameof(FeatureRow.Event1FreqLast20),
                    nameof(FeatureRow.Event1FreqLast50),
                    nameof(FeatureRow.MeanCoAppear50),
                    nameof(FeatureRow.TopCoAppear50)))
                .Append(mlContext.BinaryClassification.Trainers.FastTree(
                    labelColumnName: nameof(FeatureRow.Label),
                    featureColumnName: "Features",
                    numberOfLeaves: 20,
                    numberOfTrees: 100,
                    minimumExampleCountPerLeaf: 10,
                    learningRate: 0.2));

            trainedModel = pipeline.Fit(dataView);
        }

        public double[] ScoreNextDraw(int targetSeriesId)
        {
            var probs = new double[26];

            if (trainedModel is null || cachedHistory is null || mlContext is null)
            {
                for (int n = MIN_NUMBER; n <= MAX_NUMBER; n++)
                    probs[n] = 1.0 / 25.0;
                return probs;
            }

            // For inference, use the full cached history as the prefix.
            var inferenceRows = new List<FeatureRow>();
            for (int n = MIN_NUMBER; n <= MAX_NUMBER; n++)
                inferenceRows.Add(ComputeFeatures(cachedHistory, n));

            var inferenceView = mlContext.Data.LoadFromEnumerable(inferenceRows);
            var transformed = trainedModel.Transform(inferenceView);
            var preds = mlContext.Data
                .CreateEnumerable<PredictionRow>(transformed, reuseRowObject: false)
                .ToList();

            // Match each prediction to its number via the echoed Number column, NOT by list index —
            // CreateEnumerable order is not guaranteed to match input order.
            double total = 0.0;
            foreach (var p in preds)
            {
                int n = (int)Math.Round(p.Number);
                if (n < MIN_NUMBER || n > MAX_NUMBER) continue;
                probs[n] = Math.Max(1e-6, p.Probability);
                total += probs[n];
            }

            if (total <= 0)
            {
                for (int n = MIN_NUMBER; n <= MAX_NUMBER; n++)
                    probs[n] = 1.0 / 25.0;
            }
            else
            {
                for (int n = MIN_NUMBER; n <= MAX_NUMBER; n++)
                    probs[n] /= total;
            }

            return probs;
        }

        private static FeatureRow ComputeFeatures(IReadOnlyList<Connections.SeriesData> prefix, int n)
        {
            // Collect the indices (positions) in prefix where n appeared.
            var appearances = new List<int>();
            for (int i = 0; i < prefix.Count; i++)
            {
                if (prefix[i].AllCombinations.Any(c => c.Contains(n)))
                    appearances.Add(i);
            }

            int count = prefix.Count;

            // recency: how many series ago n last appeared; 9999 if never
            float recency = appearances.Count == 0
                ? 9999f
                : count - appearances[appearances.Count - 1];

            float appTotal = appearances.Count;

            // Frequency in the last k series
            float freq10  = appearances.Count(idx => idx >= count - 10);
            float freq20  = appearances.Count(idx => idx >= count - 20);
            float freq50  = appearances.Count(idx => idx >= count - 50);
            float freq100 = appearances.Count(idx => idx >= count - 100);

            // Gap statistics between consecutive appearances
            float meanGap = 0f;
            float gapStd  = 0f;
            if (appearances.Count >= 2)
            {
                var gaps = new List<int>(appearances.Count - 1);
                for (int j = 1; j < appearances.Count; j++)
                    gaps.Add(appearances[j] - appearances[j - 1]);

                meanGap = (float)gaps.Average();
                double sumSq = gaps.Sum(g => (double)(g - meanGap) * (g - meanGap));
                gapStd = (float)Math.Sqrt(sumSq / gaps.Count);
            }

            // Streak: walk backwards from the latest series; count consecutive series that contain n
            int streak = 0;
            for (int i = count - 1; i >= 0; i--)
            {
                if (prefix[i].AllCombinations.Any(c => c.Contains(n)))
                    streak++;
                else
                    break;
            }

            // Event-1 specific frequencies — same window logic but only event 1 (index 0).
            // These directly mirror the label definition and give FastTree signal
            // about how often n appears in event-1 specifically, not just any event.
            // Event-1 appearances for window freq counts.
            var e1appearances = new List<int>();
            for (int i = 0; i < count; i++)
                if (prefix[i].AllCombinations.Count > 0 && prefix[i].AllCombinations[0].Contains(n))
                    e1appearances.Add(i);

            int w10 = Math.Max(0, count - 10);
            int w20 = Math.Max(0, count - 20);
            int w50 = Math.Max(0, count - 50);
            int e1count10 = e1appearances.Count(idx => idx >= w10);
            int e1count20 = e1appearances.Count(idx => idx >= w20);
            int e1count50 = e1appearances.Count(idx => idx >= w50);
            int window50size = count - w50;
            int window20size = count - w20;
            int window10size = count - w10;
            float event1Freq10 = window10size > 0 ? e1count10 / (float)window10size : 0f;
            float event1Freq20 = window20size > 0 ? e1count20 / (float)window20size : 0f;
            float event1Freq50 = window50size > 0 ? e1count50 / (float)window50size : 0f;

            // Co-appearance in event-1 over last 50 series.
            // For each other number m, count series where both n and m appear in event-1.
            // meanCoAppear50 = average of those rates across all 24 partners.
            // topCoAppear50  = max rate with any single partner.
            float meanCoAppear50 = 0f;
            float topCoAppear50  = 0f;
            if (window50size > 0)
            {
                float sumRates = 0f;
                for (int m = MIN_NUMBER; m <= MAX_NUMBER; m++)
                {
                    if (m == n) continue;
                    int coCount = 0;
                    for (int i = w50; i < count; i++)
                    {
                        if (prefix[i].AllCombinations.Count == 0) continue;
                        var evt1 = prefix[i].AllCombinations[0];
                        if (evt1.Contains(n) && evt1.Contains(m)) coCount++;
                    }
                    float rate = coCount / (float)window50size;
                    sumRates += rate;
                    if (rate > topCoAppear50) topCoAppear50 = rate;
                }
                meanCoAppear50 = sumRates / 24f;
            }

            return new FeatureRow
            {
                Number           = n,
                Recency          = recency,
                FreqLast10       = freq10,
                FreqLast20       = freq20,
                FreqLast50       = freq50,
                FreqLast100      = freq100,
                MeanGap          = meanGap,
                GapStd           = gapStd,
                AppearancesTotal = appTotal,
                Streak           = (float)streak,
                Event1FreqLast10 = event1Freq10,
                Event1FreqLast20 = event1Freq20,
                Event1FreqLast50 = event1Freq50,
                MeanCoAppear50   = meanCoAppear50,
                TopCoAppear50    = topCoAppear50,
                Label            = false   // caller sets Label for training rows
            };
        }
    }
}
