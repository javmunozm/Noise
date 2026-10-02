using System;
using System.Collections.Generic;
using System.Linq;

namespace DataProcessor.Models
{
    public enum TuningObjective
    {
        Average,
        Max,
    }

    public class EnsembleScorer : INumberScorer
    {
        public string Name => "Ensemble";

        private const int MIN_NUMBER = 1;
        private const int MAX_NUMBER = 25;

        private static readonly double[] GRID_VALUES = { 0.0, 0.25, 0.5, 0.75, 1.0 };

        // Children paired with weights. Weights must sum to 1.0 (validated in setter).
        private readonly List<INumberScorer> scorers;
        private double[] weights;  // parallel to scorers, sums to 1.0

        // Cache: last training history (used by TuneWeights to retrain children at each tuning step).
        private IReadOnlyList<Connections.SeriesData>? cachedHistory;

        // Fixed weights derived from 20-series walk-forward (3200-3219).
        // Order must match the children list: [Frequency, LogReg, LSTM, FastTree, Pool].
        // FastTree leads at 70.4% after adding event-1 and co-appearance features (2026-04-29).
        public static readonly double[] FIXED_WEIGHTS_FASTTREE = { 0.0, 0.0, 0.0, 1.0, 0.0 };
        public static readonly double[] FIXED_WEIGHTS_LSTM     = { 0.0, 0.0, 1.0, 0.0, 0.0 };
        public static readonly double[] FIXED_WEIGHTS_UNIFORM  = { 0.2, 0.2, 0.2, 0.2, 0.2 };

        public EnsembleScorer(IEnumerable<INumberScorer> children)
        {
            scorers = children.ToList();
            if (scorers.Count == 0) throw new ArgumentException("EnsembleScorer needs at least one child.");
            // Default to uniform weights.
            weights = Enumerable.Repeat(1.0 / scorers.Count, scorers.Count).ToArray();
        }

        public IReadOnlyList<INumberScorer> Children => scorers;
        public IReadOnlyList<double> Weights => weights;

        public void SetWeights(double[] newWeights)
        {
            if (newWeights.Length != scorers.Count)
                throw new ArgumentException($"Expected {scorers.Count} weights, got {newWeights.Length}");
            double sum = newWeights.Sum();
            if (Math.Abs(sum - 1.0) > 1e-6)
                throw new ArgumentException($"Weights must sum to 1.0, got {sum}");
            weights = (double[])newWeights.Clone();
        }

        public void Train(IReadOnlyList<Connections.SeriesData> history)
        {
            cachedHistory = history;
            foreach (var s in scorers) s.Train(history);
        }

        public double[] ScoreNextDraw(int targetSeriesId)
        {
            // Weighted average of children's vectors. Each child returns a length-26 vector
            // with indices 1..25 summing to 1. Weighted avg also sums to 1 (no renormalization needed).
            var combined = new double[26];
            for (int i = 0; i < scorers.Count; i++)
            {
                if (weights[i] == 0) continue;  // skip dead children
                var v = scorers[i].ScoreNextDraw(targetSeriesId);
                for (int n = MIN_NUMBER; n <= MAX_NUMBER; n++)
                {
                    combined[n] += weights[i] * v[n];
                }
            }
            return combined;
        }

        public List<int> PredictTopN(int n = 14, int targetSeriesId = 0)
        {
            if (n < 1 || n > MAX_NUMBER) throw new ArgumentOutOfRangeException(nameof(n));
            var probs = ScoreNextDraw(targetSeriesId);
            // Top n indices (1..25) by probability, sorted ascending.
            return Enumerable.Range(MIN_NUMBER, MAX_NUMBER - MIN_NUMBER + 1)
                .OrderByDescending(num => probs[num])
                .Take(n)
                .OrderBy(num => num)
                .ToList();
        }

        /// <summary>
        /// Compute weights proportional to each model's mean avg-score over the last
        /// <paramref name="windowSize"/> series in <paramref name="recentScores"/>.
        /// recentScores[i][j] = avg score of child j on the i-th most-recent series.
        /// Applies softmax-style temperature scaling so the best model gets more weight
        /// without completely collapsing to winner-take-all.
        /// Sets weights on this instance and returns them.
        /// </summary>
        public double[] ComputeDynamicWeights(List<double[]> recentScores, int windowSize = 5, double temperature = 4.0)
        {
            int k = scorers.Count;
            var window = recentScores.TakeLast(windowSize).ToList();
            if (window.Count == 0)
            {
                var uniform = Enumerable.Repeat(1.0 / k, k).ToArray();
                SetWeights(uniform);
                return uniform;
            }

            // Mean avg score per child over the window.
            var meanScores = new double[k];
            for (int i = 0; i < k; i++)
                meanScores[i] = window.Average(row => row[i]);

            // Softmax with temperature: w[i] = exp(score[i]*T) / sum(exp(score[j]*T))
            // Temperature > 1 sharpens the distribution toward the best model.
            double maxScore = meanScores.Max();
            var expScores = meanScores.Select(s => Math.Exp((s - maxScore) * temperature)).ToArray();
            double expSum = expScores.Sum();
            var w = expScores.Select(e => e / expSum).ToArray();

            SetWeights(w);
            return w;
        }

        /// <summary>
        /// Grid search over weight tuples in {0, 0.25, 0.5, 0.75, 1.0}^children, filtered to sum=1.
        /// For each candidate weight tuple, evaluates on the tuning window: for each tuning series s,
        /// trains children on history strictly before s, predicts top-14, compares to actual events in s,
        /// records best-match across the events of s. The aggregate over tuning series depends on
        /// <paramref name="objective"/>: Average optimizes mean best-match, Max optimizes peak best-match
        /// (chase-the-best). Ties broken by lower L2 distance from uniform (preferring more balanced ensembles).
        /// </summary>
        /// <param name="fullHistory">All series available (training + tuning). Each tuning step uses
        /// only the prefix strictly before that series.</param>
        /// <param name="tuningSeriesIds">Series IDs to tune on (e.g., 3205..3210). Must be present in fullHistory.</param>
        /// <param name="objective">Aggregation: Average best-match across the window, or Max best-match.</param>
        /// <returns>The best weight tuple found, also applied to this instance via SetWeights.</returns>
        public double[] TuneWeights(IReadOnlyList<Connections.SeriesData> fullHistory, IReadOnlyList<int> tuningSeriesIds, TuningObjective objective = TuningObjective.Average)
        {
            int k = scorers.Count;

            // 1. Generate all candidate weight tuples from GRID_VALUES^k filtered to sum=1.
            var candidates = GenerateWeightCandidates(k);

            // 2. For each tuning series: train children once, snapshot their probability vectors.
            // snapshots[seriesIndex][scorerIndex] = double[26] probability vector
            var snapshots = new List<double[][]>();
            var tuningActuals = new List<List<List<int>>>();

            foreach (int sid in tuningSeriesIds)
            {
                // a. Prefix history: all series strictly before this one.
                var prefix = fullHistory.Where(x => x.SeriesId < sid).ToList();

                // b. Actual events for this tuning series.
                var tuningSeries = fullHistory.FirstOrDefault(x => x.SeriesId == sid);
                if (tuningSeries == null)
                {
                    Console.WriteLine($"  Tuning {sid}: WARNING — series not found in fullHistory, skipping.");
                    continue;
                }
                tuningActuals.Add(tuningSeries.AllCombinations);

                // c. Retrain each child on prefix (once per series).
                foreach (var scorer in scorers)
                    scorer.Train(prefix);

                // d. Snapshot each child's ScoreNextDraw(sid) — call once and reuse for ALL weight candidates.
                var seriesSnapshots = new double[scorers.Count][];
                for (int i = 0; i < scorers.Count; i++)
                    seriesSnapshots[i] = scorers[i].ScoreNextDraw(sid);

                snapshots.Add(seriesSnapshots);
                Console.WriteLine($"  Tuning {sid}: trained children, snapshotted {scorers.Count} scorer outputs");
            }

            int numTuningSeries = snapshots.Count;
            if (numTuningSeries == 0)
                throw new InvalidOperationException("No valid tuning series found in fullHistory.");

            // 3. Evaluate each candidate weight tuple.
            double bestScore = double.NegativeInfinity;
            double[] bestWeights = candidates[0];

            double uniformL2 = 1.0 / k;

            foreach (var candidate in candidates)
            {
                double aggregate = objective == TuningObjective.Max ? double.NegativeInfinity : 0.0;

                for (int si = 0; si < numTuningSeries; si++)
                {
                    // a. Compute ensemble probability vector for this series using candidate weights.
                    var combined = new double[26];
                    for (int i = 0; i < k; i++)
                    {
                        if (candidate[i] == 0) continue;
                        var v = snapshots[si][i];
                        for (int n = MIN_NUMBER; n <= MAX_NUMBER; n++)
                            combined[n] += candidate[i] * v[n];
                    }

                    // b. Take top-14 indices by probability.
                    var top14 = Enumerable.Range(MIN_NUMBER, MAX_NUMBER - MIN_NUMBER + 1)
                        .OrderByDescending(num => combined[num])
                        .Take(14)
                        .ToHashSet();

                    // c. Best-match = max over events of |top14 ∩ event| / 14.0
                    double seriesBestMatch = 0.0;
                    foreach (var evt in tuningActuals[si])
                    {
                        int intersection = evt.Count(x => top14.Contains(x));
                        double match = intersection / 14.0;
                        if (match > seriesBestMatch) seriesBestMatch = match;
                    }

                    if (objective == TuningObjective.Max)
                    {
                        if (seriesBestMatch > aggregate) aggregate = seriesBestMatch;
                    }
                    else
                    {
                        aggregate += seriesBestMatch;
                    }
                }

                double score = objective == TuningObjective.Max ? aggregate : aggregate / numTuningSeries;

                // 4. Pick best; tie-break by L2 distance from uniform.
                bool better = score > bestScore;
                if (!better && Math.Abs(score - bestScore) < 1e-12)
                {
                    // Tie: prefer closer to uniform (lower L2).
                    double currentL2 = L2FromUniform(bestWeights, uniformL2);
                    double newL2 = L2FromUniform(candidate, uniformL2);
                    better = newL2 < currentL2;
                }

                if (better)
                {
                    bestScore = score;
                    bestWeights = candidate;
                }
            }

            // 5. Apply and report.
            SetWeights(bestWeights);
            string weightStr = string.Join(", ", bestWeights.Select(w => w.ToString("F2")));
            string objLabel = objective == TuningObjective.Max ? "max best-match" : "avg best-match";
            Console.WriteLine($"Tuned weights: [{weightStr}], {objLabel} = {bestScore:P1}");

            return (double[])bestWeights.Clone();
        }

        // Generates all k-tuples from GRID_VALUES whose elements sum to 1.0 (within 1e-9).
        private List<double[]> GenerateWeightCandidates(int k)
        {
            var result = new List<double[]>();
            var current = new double[k];
            GenerateRecursive(result, current, k, 0, 0.0);
            return result;
        }

        private void GenerateRecursive(List<double[]> result, double[] current, int k, int depth, double runningSum)
        {
            if (depth == k)
            {
                if (Math.Abs(runningSum - 1.0) < 1e-9)
                    result.Add((double[])current.Clone());
                return;
            }
            foreach (double v in GRID_VALUES)
            {
                // Pruning: if running sum already exceeds 1, skip remaining values >= current v
                if (runningSum + v > 1.0 + 1e-9) break;
                current[depth] = v;
                GenerateRecursive(result, current, k, depth + 1, runningSum + v);
            }
        }

        private static double L2FromUniform(double[] w, double uniformValue)
        {
            double sum = 0;
            foreach (double wi in w)
            {
                double diff = wi - uniformValue;
                sum += diff * diff;
            }
            return Math.Sqrt(sum);
        }
    }
}
