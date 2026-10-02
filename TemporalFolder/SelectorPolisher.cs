using System;
using System.Collections.Generic;
using System.Linq;

namespace DataProcessor.Models
{
    public class SelectorPolisher
    {
        private const int MIN_NUMBER = 1;
        private const int MAX_NUMBER = 25;

        // Veto component weights (sum = 1.0)
        private const double V1_WEIGHT = 0.40; // PoolDisagreement
        private const double V2_WEIGHT = 0.35; // RawConfidenceMargin
        private const double V3_WEIGHT = 0.25; // RecentEvent1Streak

        private readonly double vetoThreshold;
        private readonly int minVetoComponents;

        public SelectorPolisher(double vetoThreshold = 0.60,
                                int minVetoComponents = 2)
        {
            this.vetoThreshold = vetoThreshold;
            this.minVetoComponents = minVetoComponents;
        }

        public List<int> Apply(
            double[] ftProbs,
            double[] poolProbs,
            List<int> rawTop14,
            List<int> refinedTop14,
            int targetSid,
            IReadOnlyList<Connections.SeriesData> history,
            out List<string> vetoActions)
        {
            vetoActions = new List<string>();

            var rawSet     = rawTop14.ToHashSet();
            var refinedSet = refinedTop14.ToHashSet();

            // Identify the refiner's swaps: pairs of (swap_out, swap_in).
            var swapOuts = rawSet.Except(refinedSet).OrderBy(n => n).ToList();
            var swapIns  = refinedSet.Except(rawSet).OrderBy(n => n).ToList();

            // No swaps to evaluate — output equals refiner.
            if (swapOuts.Count == 0 || swapIns.Count == 0)
                return refinedTop14.OrderBy(n => n).ToList();

            // PoolScorer top-14 by probability (used by V1).
            var poolRank = ComputePoolRank(poolProbs);

            // Recent event-1 history (last 5 series, descending).
            var prefix = history
                .Where(s => s.SeriesId < targetSid)
                .OrderByDescending(s => s.SeriesId)
                .ToList();

            // Pair swap-outs with swap-ins by index. The refiner doesn't expose
            // its actual pairing, but the symmetry of OrderBy(n) keeps this stable.
            var working = new HashSet<int>(refinedTop14);
            int pairs = Math.Min(swapOuts.Count, swapIns.Count);

            for (int i = 0; i < pairs; i++)
            {
                int swapOut = swapOuts[i];
                int swapIn  = swapIns[i];

                double v1 = PoolDisagreement(swapIn, swapOut, poolRank);
                double v2 = RawConfidenceMargin(swapIn, swapOut, ftProbs);
                double v3 = RecentEvent1Streak(swapIn, swapOut, prefix);

                double total = V1_WEIGHT * v1 + V2_WEIGHT * v2 + V3_WEIGHT * v3;
                int components = (v1 > 0 ? 1 : 0) + (v2 > 0 ? 1 : 0) + (v3 > 0 ? 1 : 0);

                if (total >= vetoThreshold && components >= minVetoComponents)
                {
                    // Veto this swap: revert swap_in, restore swap_out.
                    working.Remove(swapIn);
                    working.Add(swapOut);
                    vetoActions.Add($"+{swapOut:D2} -{swapIn:D2} (score={total:F2},c={components})");
                }
            }

            return working.OrderBy(n => n).ToList();
        }

        // ------------------------------------------------------------------
        // Veto components
        // ------------------------------------------------------------------

        private static double PoolDisagreement(int swapIn, int swapOut, int[] poolRank)
        {
            // Pool prefers swap_out over swap_in: signal that the refiner removed a number
            // Pool ranks higher than the replacement. Normalized by /5 since refiner swaps
            // typically happen near the cut, where rank gaps of 3–5 are meaningful.
            int rankIn  = poolRank[swapIn];
            int rankOut = poolRank[swapOut];

            if (rankOut < rankIn) // lower rank index = higher pool prob
                return Math.Min(1.0, (rankIn - rankOut) / 5.0);
            return 0.0;
        }

        private static double RawConfidenceMargin(int swapIn, int swapOut, double[] ftProbs)
        {
            // FastTree was meaningfully more confident in the swap-out than the swap-in,
            // before bias adjustment. Tuned for cut-margin zone where ratios are typically
            // 1.00–1.10; trigger at 5% gap, saturate at 20%.
            double pIn  = ftProbs[swapIn];
            double pOut = ftProbs[swapOut];

            if (pIn <= 0) return 0.0;

            double ratio = pOut / pIn;
            if (ratio > 1.05)
                return Math.Min(1.0, (ratio - 1.05) / 0.15);
            return 0.0;
        }

        private static double RecentEvent1Streak(
            int swapIn, int swapOut,
            IReadOnlyList<Connections.SeriesData> prefixDesc)
        {
            int recentDrawnOut = prefixDesc.Take(5)
                .Count(s => s.AllCombinations.Count > 0 && s.AllCombinations[0].Contains(swapOut));
            int recentDrawnIn  = prefixDesc.Take(5)
                .Count(s => s.AllCombinations.Count > 0 && s.AllCombinations[0].Contains(swapIn));

            if (recentDrawnOut >= 3 && recentDrawnIn <= 1) return 1.0;
            if (recentDrawnOut >= 2 && recentDrawnIn == 0) return 0.5;
            // New tier: swap-out drawn at least once recently while swap-in has never appeared.
            if (recentDrawnOut >= 1 && recentDrawnIn == 0) return 0.25;
            return 0.0;
        }

        // ------------------------------------------------------------------
        // Helpers
        // ------------------------------------------------------------------

        // Returns rank[n] = 1-based position of number n in pool top-25 by probability.
        // Number with the highest pool prob has rank 1; lowest has rank 25.
        private static int[] ComputePoolRank(double[] poolProbs)
        {
            var ranked = Enumerable.Range(MIN_NUMBER, MAX_NUMBER - MIN_NUMBER + 1)
                .OrderByDescending(n => poolProbs[n])
                .ToList();

            var rank = new int[26];
            for (int i = 0; i < ranked.Count; i++)
                rank[ranked[i]] = i + 1;
            return rank;
        }
    }
}
