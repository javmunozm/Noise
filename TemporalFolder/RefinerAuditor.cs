using System;
using System.Collections.Generic;
using System.Linq;

namespace DataProcessor.Models
{
    public class RefinerAuditor
    {
        private const int MIN_NUMBER = 1;
        private const int MAX_NUMBER = 25;

        // Risk component weights (sum = 1.0)
        private const double W1 = 0.30; // StaleAnchorRisk
        private const double W2 = 0.20; // ClusterImbalanceRisk
        private const double W3 = 0.30; // ContextDriftRisk
        private const double W4 = 0.20; // SwapInstabilityRisk

        // Rescue component weights (sum = 1.0)
        private const double W5 = 0.35; // CoverageGapFill
        private const double W6 = 0.40; // RecentDrawAgreement
        private const double W7 = 0.25; // MissedByBothModels

        private readonly double riskThreshold;
        private readonly double rescueThreshold;
        private readonly int minRiskComponents;
        private readonly int minRescueComponents;

        public RefinerAuditor(double riskThreshold = 0.50,
                              double rescueThreshold = 0.40,
                              int minRiskComponents = 2,
                              int minRescueComponents = 2)
        {
            this.riskThreshold     = riskThreshold;
            this.rescueThreshold   = rescueThreshold;
            this.minRiskComponents = minRiskComponents;
            this.minRescueComponents = minRescueComponents;
        }

        public List<int> Apply(
            double[] adjustedProbs,
            List<int> rawTop14,
            List<int> refinedTop14,
            double[] poolProbs,
            double[] biasVector,
            int targetSid,
            IReadOnlyList<Connections.SeriesData> history,
            IReadOnlyList<List<int>> recentRefinerSwaps,
            out string? auditAction)
        {
            auditAction = null;

            var refinedSet = refinedTop14.ToHashSet();
            var rawSet     = rawTop14.ToHashSet();

            // Numbers the refiner deliberately swapped IN are off-limits for Stage 1 eviction.
            // The auditor must not undo the refiner's own decisions — it targets numbers the
            // refiner left untouched that are structurally risky.
            var refinerSwapIns = refinedSet.Except(rawSet).ToHashSet();

            // Pool top-14 derived from poolProbs.
            var poolTop14Set = Enumerable.Range(MIN_NUMBER, MAX_NUMBER - MIN_NUMBER + 1)
                .OrderByDescending(n => poolProbs[n])
                .Take(14)
                .ToHashSet();

            // Quintile counts for refined set.
            var refinedQuint = new int[6]; // 1-indexed: quintile 1..5
            foreach (int n in refinedTop14)
                refinedQuint[Quintile(n)]++;

            // History prefix strictly before targetSid, newest-first.
            var prefix = history
                .Where(s => s.SeriesId < targetSid)
                .OrderByDescending(s => s.SeriesId)
                .ToList();

            // ---------- Stage 1: Risk score each refined pick ----------

            double worstScore = -1;
            int worstPick = -1;
            int worstComponents = 0;

            foreach (int n in refinedTop14)
            {
                // Skip numbers the refiner deliberately swapped in — auditor doesn't undo refiner picks.
                if (refinerSwapIns.Contains(n)) continue;

                double s1 = StaleAnchorRisk(n, adjustedProbs, refinedTop14, prefix, biasVector);
                double s2 = ClusterImbalanceRisk(n, refinedQuint);
                double s3 = ContextDriftRisk(n, biasVector, prefix, adjustedProbs, refinedTop14);
                double s4 = SwapInstabilityRisk(n, recentRefinerSwaps);

                double total = W1 * s1 + W2 * s2 + W3 * s3 + W4 * s4;
                int components = (s1 > 0 ? 1 : 0) + (s2 > 0 ? 1 : 0) + (s3 > 0 ? 1 : 0) + (s4 > 0 ? 1 : 0);

                if (total > worstScore)
                {
                    worstScore = total;
                    worstPick = n;
                    worstComponents = components;
                }
            }

            if (worstPick < 0 || worstScore < riskThreshold || worstComponents < minRiskComponents)
                return refinedTop14;

            // ---------- Stage 2: Rescue score each non-refined number ----------

            double bestRescueScore = -1;
            int bestRescue = -1;
            int bestRescueComponents = 0;

            // Numbers the refiner deliberately swapped OUT are also off-limits for rescue —
            // the refiner already evaluated and rejected them.
            var refinerSwapOuts = rawSet.Except(refinedSet).ToHashSet();

            foreach (int n in Enumerable.Range(MIN_NUMBER, MAX_NUMBER - MIN_NUMBER + 1))
            {
                if (refinedSet.Contains(n)) continue;
                if (refinerSwapOuts.Contains(n)) continue;

                double r1 = CoverageGapFill(n, refinedQuint);
                double r2 = RecentDrawAgreement(n, prefix, 3);
                double r3 = MissedByBothModels(n, rawSet, poolTop14Set, prefix);

                double total = W5 * r1 + W6 * r2 + W7 * r3;
                int components = (r1 > 0 ? 1 : 0) + (r2 > 0 ? 1 : 0) + (r3 > 0 ? 1 : 0);

                if (total > bestRescueScore)
                {
                    bestRescueScore = total;
                    bestRescue = n;
                    bestRescueComponents = components;
                }
            }

            if (bestRescue < 0 || bestRescueScore < rescueThreshold || bestRescueComponents < minRescueComponents)
                return refinedTop14;

            // ---------- Stage 3: Single substitution ----------

            var result = refinedTop14.ToList();
            result.Remove(worstPick);
            result.Add(bestRescue);
            result.Sort();

            auditAction = $"-{worstPick:D2}(risk={worstScore:F2},c={worstComponents}) +{bestRescue:D2}(rescue={bestRescueScore:F2},c={bestRescueComponents})";
            return result;
        }

        // ------------------------------------------------------------------
        // Risk components
        // ------------------------------------------------------------------

        private static double StaleAnchorRisk(
            int n,
            double[] adjustedProbs,
            List<int> refinedTop14,
            IReadOnlyList<Connections.SeriesData> prefixDesc,
            double[] biasVector)
        {
            // Numbers with positive bias are known under-rankers — the refiner already accounts
            // for them. Flagging them as stale anchors would double-count the refiner's signal.
            double bias = biasVector.Length > n ? biasVector[n] : 0.0;
            if (bias > 0) return 0.0;

            // Bottom 30% of refinedTop14 by adjusted prob = weakest 4 of 14.
            var sorted = refinedTop14.OrderBy(x => adjustedProbs[x]).ToList();
            int bottom = (int)Math.Ceiling(refinedTop14.Count * 0.30);
            if (!sorted.Take(bottom).Contains(n)) return 0.0;

            // Not drawn in event-1 in any of the last 5 series.
            int recentDrawn = prefixDesc.Take(5)
                .Count(s => s.AllCombinations.Count > 0 && s.AllCombinations[0].Contains(n));
            return recentDrawn == 0 ? 1.0 : 0.0;
        }

        private static double ClusterImbalanceRisk(int n, int[] refinedQuint)
        {
            int q = Quintile(n);
            int count = refinedQuint[q];
            if (count >= 4) return 1.0;
            if (count == 3) return 0.4;
            return 0.0;
        }

        private static double ContextDriftRisk(
            int n,
            double[] biasVector,
            IReadOnlyList<Connections.SeriesData> prefixDesc,
            double[] adjustedProbs,
            List<int> refinedTop14)
        {
            double bias = biasVector.Length > n ? biasVector[n] : 0.0;
            double freq = RecentEvent1Freq(n, prefixDesc, 10);

            // Positive bias + very hot recent freq: bias may be overshooting.
            // But only flag if the number is also ranking weakly (bottom half of refined set
            // by adjusted prob) — if it's ranking well, the bias is working as intended.
            if (bias > 0.05 && freq > 0.70)
            {
                var sorted = refinedTop14.OrderBy(x => adjustedProbs[x]).ToList();
                int halfMark = refinedTop14.Count / 2;
                if (!sorted.Take(halfMark).Contains(n)) return 0.0; // ranking fine — no drift
                return Math.Min(1.0, (freq - 0.70) / 0.30);
            }

            // Negative bias + high recent freq: bias is suppressing a currently active number.
            if (bias < -0.05 && freq > 0.50)
                return Math.Min(1.0, (freq - 0.50) / 0.50);

            return 0.0;
        }

        private static double SwapInstabilityRisk(int n, IReadOnlyList<List<int>> recentRefinerSwaps)
        {
            // Count how many of the last 5 refiner swap-in lists contained n.
            int c = recentRefinerSwaps.TakeLast(5).Count(swapIn => swapIn.Contains(n));
            if (c >= 3) return 1.0;
            if (c == 2) return 0.4;
            return 0.0;
        }

        // ------------------------------------------------------------------
        // Rescue components
        // ------------------------------------------------------------------

        private static double CoverageGapFill(int n, int[] refinedQuint)
        {
            int q = Quintile(n);
            int count = refinedQuint[q];
            if (count <= 1) return 1.0;
            if (count == 2) return 0.4;
            return 0.0;
        }

        private static double RecentDrawAgreement(
            int n,
            IReadOnlyList<Connections.SeriesData> prefixDesc,
            int window)
        {
            int drawn = prefixDesc.Take(window)
                .Count(s => s.AllCombinations.Count > 0 && s.AllCombinations[0].Contains(n));
            return drawn / (double)Math.Max(1, Math.Min(window, prefixDesc.Count));
        }

        private static double MissedByBothModels(
            int n,
            HashSet<int> rawTop14,
            HashSet<int> poolTop14,
            IReadOnlyList<Connections.SeriesData> prefixDesc)
        {
            if (rawTop14.Contains(n)) return 0.0;
            if (poolTop14.Contains(n)) return 0.0;
            // Both models missed it — signal if it appeared recently in event-1.
            int drawn = prefixDesc.Take(2)
                .Count(s => s.AllCombinations.Count > 0 && s.AllCombinations[0].Contains(n));
            return drawn / 2.0;
        }

        // ------------------------------------------------------------------
        // Helpers
        // ------------------------------------------------------------------

        private static int Quintile(int n)
        {
            // 1={1-5}, 2={6-10}, 3={11-15}, 4={16-20}, 5={21-25}
            return (n - 1) / 5 + 1;
        }

        private static double RecentEvent1Freq(
            int n,
            IReadOnlyList<Connections.SeriesData> prefixDesc,
            int window)
        {
            int take = Math.Min(window, prefixDesc.Count);
            if (take == 0) return 0.0;
            int drawn = prefixDesc.Take(take)
                .Count(s => s.AllCombinations.Count > 0 && s.AllCombinations[0].Contains(n));
            return drawn / (double)take;
        }
    }
}
