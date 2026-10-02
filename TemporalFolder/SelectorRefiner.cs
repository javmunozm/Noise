using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.IO;

namespace DataProcessor.Models
{
    public record BiasCalibration(
        [property: JsonPropertyName("bias_vector")]       double[] PerNumberAdditiveBias,
        [property: JsonPropertyName("calibration_window_start")] int CalibrationWindowStart,
        [property: JsonPropertyName("calibration_window_end")]   int CalibrationWindowEnd,
        [property: JsonPropertyName("swap_margin")]       double SwapMargin,
        [property: JsonPropertyName("max_swaps")]         int MaxSwaps
    );

    public class SelectorRefiner
    {
        private const int MIN_NUMBER = 1;
        private const int MAX_NUMBER = 25;
        private const int RECENCY_WINDOW = 50;
        private const double OVERDUE_THRESHOLD = 1.5;

        private readonly BiasCalibration calibration;

        public SelectorRefiner(BiasCalibration calibration)
        {
            this.calibration = calibration;
        }

        public List<int> Refine(double[] probs, int targetSid,
                                IReadOnlyList<Connections.SeriesData> history)
        {
            if (probs.Length != 26)
                throw new ArgumentException("probs must have length 26 (indices 0..25; index 0 unused).");

            // Stage 1 — multiplicative bias correction.
            var adjusted = new double[26];
            for (int n = MIN_NUMBER; n <= MAX_NUMBER; n++)
                adjusted[n] = probs[n] * (1.0 + calibration.PerNumberAdditiveBias[n]);

            var ranked = Enumerable.Range(MIN_NUMBER, MAX_NUMBER - MIN_NUMBER + 1)
                .OrderByDescending(n => adjusted[n])
                .ToList();

            double cutValue = adjusted[ranked[13]]; // 14th-place adjusted prob

            // Stage 2 — swap-margin filter.
            // Only numbers within SwapMargin * cutValue of the cut are eligible to swap.
            double margin = calibration.SwapMargin * Math.Abs(cutValue);

            var result = ranked.Take(14).ToHashSet();

            // Swap-out candidates: in top-14, adjusted prob within margin of cut.
            // Sorted by adjusted prob ascending (weakest hold first).
            var swapOut = ranked
                .Take(14)
                .Where(n => adjusted[n] <= cutValue + margin)
                .OrderBy(n => adjusted[n])
                .ToList();

            // Swap-in candidates: outside top-14, adjusted prob within margin of cut.
            // Sorted by adjusted prob descending (strongest challenger first).
            var swapIn = ranked
                .Skip(14)
                .Where(n => adjusted[n] >= cutValue - margin)
                .OrderByDescending(n => adjusted[n])
                .ToList();

            if (swapOut.Count == 0 || swapIn.Count == 0)
                return ranked.Take(14).OrderBy(n => n).ToList();

            // Stage 3 — recency-aware swap budget.
            // Among borderline numbers, prefer overdue swap-ins and stale swap-outs.
            var prefix   = history.Where(s => s.SeriesId < targetSid)
                                  .OrderByDescending(s => s.SeriesId)
                                  .ToList();
            var window50 = prefix.Take(RECENCY_WINDOW).ToList();

            double OverdueScore(int n)
            {
                if (window50.Count == 0) return 0;
                int appearances = window50.Count(s =>
                    s.AllCombinations.Count > 0 && s.AllCombinations[0].Contains(n));
                if (appearances == 0) return 0;
                double freq        = appearances / (double)window50.Count;
                double expectedGap = 1.0 / freq;
                int lastIdx = prefix.FindIndex(s =>
                    s.AllCombinations.Count > 0 && s.AllCombinations[0].Contains(n));
                if (lastIdx < 0) return 0;
                double gap = lastIdx + 1;
                // Returns how many multiples of expected gap the number is overdue (0 if not overdue).
                return gap > OVERDUE_THRESHOLD * expectedGap ? gap / expectedGap : 0;
            }

            // Re-order swap-in by overdue score desc, then adjusted prob desc.
            swapIn = swapIn
                .OrderByDescending(n => OverdueScore(n))
                .ThenByDescending(n => adjusted[n])
                .ToList();

            // Re-order swap-out by overdue score asc (least overdue = most stale = evict first),
            // then adjusted prob asc.
            swapOut = swapOut
                .OrderBy(n => OverdueScore(n))
                .ThenBy(n => adjusted[n])
                .ToList();

            // Conviction gate — only swap when evidence is overwhelming:
            //   - swap-in must be genuinely overdue (OverdueScore >= 1)
            //   - swap-out must not be overdue (OverdueScore == 0)
            // This prevents the refiner from firing borderline swaps on series where
            // raw FastTree was already correct.
            int swaps = 0;
            int pairs = Math.Min(swapOut.Count, swapIn.Count);
            for (int i = 0; i < pairs && swaps < calibration.MaxSwaps; i++)
            {
                if (OverdueScore(swapIn[i]) < 1.0) break;       // no more strong-conviction swap-ins
                if (OverdueScore(swapOut[i]) > 0.0) continue;   // this swap-out is overdue, skip it

                result.Remove(swapOut[i]);
                result.Add(swapIn[i]);
                swaps++;
            }

            return result.OrderBy(n => n).ToList();
        }

        public static SelectorRefiner LoadFromJson(string path)
        {
            string json = File.ReadAllText(path);
            var cal = JsonSerializer.Deserialize<BiasCalibration>(json)
                ?? throw new InvalidOperationException($"Failed to deserialize BiasCalibration from {path}");
            return new SelectorRefiner(cal);
        }
    }
}
