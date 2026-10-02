#!/usr/bin/env python3
"""
COMPREHENSIVE LOTTERY PREDICTION STUDY
Testing multiple advanced strategies to surpass 72.79% baseline

Strategies:
1. Pure Random (Baseline)
2. Mandel-Style (Current Champion - 72.79%)
3. Frequency-Weighted Selection
4. Pair Affinity Optimization
5. Hot/Cold Number Analysis
6. Genetic Algorithm Evolution
7. Critical Number Forcing
8. Ensemble ML (Multiple Seeds)
9. Pattern-Based Filtering
10. Hybrid Super-Strategy
"""

import json
import random
from collections import defaultdict, Counter
from datetime import datetime
from true_learning_model import TrueLearningModel
import itertools


def load_series_data(file_path="full_series_data.json"):
    """Load full series data from JSON"""
    with open(file_path, 'r') as f:
        return json.load(f)


def test_combination_fast(combination, all_series_data, test_series_ids):
    """Fast testing - return average best match"""
    total_best = 0
    for series_id in test_series_ids:
        actual_events = all_series_data[str(series_id)]
        best_match = max(len(set(combination) & set(event)) for event in actual_events)
        total_best += best_match
    return total_best / len(test_series_ids)


class AdvancedStrategyTester:
    def __init__(self, all_series_data, test_series_ids, training_series_ids):
        self.all_series_data = all_series_data
        self.test_series_ids = test_series_ids
        self.training_series_ids = training_series_ids

        # Pre-calculate statistics
        self.calculate_statistics()

    def calculate_statistics(self):
        """Calculate all necessary statistics from training data"""
        print("Calculating historical statistics...")

        # Number frequencies
        self.number_freq = Counter()
        self.recent_freq = Counter()  # Last 20 series
        self.pair_freq = Counter()

        # Position tracking
        self.position_freq = defaultdict(Counter)

        # Get recent series for hot/cold
        recent_series = [s for s in self.training_series_ids if s >= max(self.training_series_ids) - 20]

        for series_id in self.training_series_ids:
            events = self.all_series_data[str(series_id)]
            is_recent = series_id in recent_series

            for event in events:
                # Overall frequency
                for num in event:
                    self.number_freq[num] += 1
                    if is_recent:
                        self.recent_freq[num] += 1

                # Pair frequencies
                for i, num1 in enumerate(event):
                    for num2 in event[i+1:]:
                        pair = tuple(sorted([num1, num2]))
                        self.pair_freq[pair] += 1

                # Position frequencies
                for pos, num in enumerate(sorted(event)):
                    self.position_freq[pos][num] += 1

        # Hot/Cold analysis
        total_recent = sum(self.recent_freq.values())
        total_overall = sum(self.number_freq.values())

        self.hot_numbers = []
        self.cold_numbers = []

        for num in range(1, 26):
            recent_pct = self.recent_freq[num] / total_recent if total_recent > 0 else 0
            overall_pct = self.number_freq[num] / total_overall if total_overall > 0 else 0

            if recent_pct > overall_pct * 1.2:
                self.hot_numbers.append(num)
            elif recent_pct < overall_pct * 0.8:
                self.cold_numbers.append(num)

        # Critical numbers (appear very frequently)
        avg_freq = sum(self.number_freq.values()) / len(self.number_freq)
        self.critical_numbers = [num for num, freq in self.number_freq.items() if freq > avg_freq * 1.2]

        print(f"  Number frequencies calculated")
        print(f"  Hot numbers: {len(self.hot_numbers)}")
        print(f"  Cold numbers: {len(self.cold_numbers)}")
        print(f"  Critical numbers: {len(self.critical_numbers)}")
        print(f"  Pair frequencies: {len(self.pair_freq)}")

    def strategy_1_pure_random(self, count=5000):
        """Strategy 1: Pure random selection (baseline)"""
        print("\n[1/10] Pure Random Selection...")
        candidates = []
        for _ in range(count):
            candidates.append(sorted(random.sample(range(1, 26), 14)))
        return candidates

    def strategy_2_mandel_style(self, count=5000):
        """Strategy 2: Current Mandel approach (champion baseline)"""
        print("\n[2/10] Mandel-Style Random + ML Variations...")
        candidates = []

        # Half pure random
        for _ in range(count // 2):
            candidates.append(sorted(random.sample(range(1, 26), 14)))

        # Half ML-guided
        model = TrueLearningModel(seed=650)
        for series_id in self.training_series_ids:
            events = self.all_series_data[str(series_id)]
            model.learn_from_series(series_id, events)

        ml_base = model.predict_best_combination(3151)
        for _ in range(count // 2):
            variant = list(ml_base)
            for _ in range(random.randint(1, 3)):
                idx = random.randint(0, 13)
                new_num = random.randint(1, 25)
                if new_num not in variant:
                    variant[idx] = new_num
            candidates.append(sorted(variant))

        return candidates

    def strategy_3_frequency_weighted(self, count=5000):
        """Strategy 3: Frequency-weighted selection"""
        print("\n[3/10] Frequency-Weighted Selection...")
        candidates = []

        # Create weighted pool
        weights = [self.number_freq[i] for i in range(1, 26)]
        total_weight = sum(weights)
        probabilities = [w / total_weight for w in weights]

        for _ in range(count):
            selected = []
            available = list(range(1, 26))
            avail_probs = probabilities[:]

            for _ in range(14):
                # Weighted random choice
                choice_idx = random.choices(range(len(available)), weights=avail_probs)[0]
                selected.append(available[choice_idx])
                available.pop(choice_idx)
                avail_probs.pop(choice_idx)
                # Renormalize
                if sum(avail_probs) > 0:
                    total = sum(avail_probs)
                    avail_probs = [p / total for p in avail_probs]

            candidates.append(sorted(selected))

        return candidates

    def strategy_4_pair_affinity(self, count=5000):
        """Strategy 4: Pair affinity optimization"""
        print("\n[4/10] Pair Affinity Optimization...")
        candidates = []

        # Get top pairs
        top_pairs = sorted(self.pair_freq.items(), key=lambda x: x[1], reverse=True)[:50]

        for _ in range(count):
            selected = set()

            # Start with a strong pair
            if top_pairs:
                pair, _ = random.choice(top_pairs[:10])
                selected.update(pair)

            # Add numbers that have good affinity with existing
            while len(selected) < 14:
                candidates_scores = []
                for num in range(1, 26):
                    if num in selected:
                        continue
                    score = sum(self.pair_freq[tuple(sorted([num, s]))] for s in selected)
                    candidates_scores.append((num, score))

                if candidates_scores:
                    # Weighted choice based on scores
                    nums, scores = zip(*candidates_scores)
                    total = sum(scores) if sum(scores) > 0 else 1
                    probs = [s / total for s in scores]
                    choice = random.choices(nums, weights=probs)[0]
                    selected.add(choice)
                else:
                    # Fallback to random
                    available = [n for n in range(1, 26) if n not in selected]
                    if available:
                        selected.add(random.choice(available))

            candidates.append(sorted(list(selected)))

        return candidates

    def strategy_5_hot_cold(self, count=5000):
        """Strategy 5: Hot/Cold number balance"""
        print("\n[5/10] Hot/Cold Number Balance...")
        candidates = []

        # If no hot/cold detected, use frequency ranking
        if not self.hot_numbers:
            self.hot_numbers = [num for num, freq in self.number_freq.most_common(12)]
        if not self.cold_numbers:
            self.cold_numbers = [num for num, freq in sorted(self.number_freq.items(), key=lambda x: x[1])[:6]]

        for _ in range(count):
            selected = []

            # Include 5-7 hot numbers
            if self.hot_numbers:
                num_hot = random.randint(5, min(7, len(self.hot_numbers)))
                selected.extend(random.sample(self.hot_numbers, num_hot))

            # Include 1-2 cold numbers (contrarian)
            if self.cold_numbers:
                num_cold = random.randint(1, min(2, len(self.cold_numbers)))
                cold_picks = [c for c in self.cold_numbers if c not in selected]
                if len(cold_picks) >= num_cold:
                    selected.extend(random.sample(cold_picks, num_cold))

            # Fill rest with medium numbers
            remaining = [n for n in range(1, 26) if n not in selected
                        and n not in self.hot_numbers and n not in self.cold_numbers]
            needed = 14 - len(selected)
            if len(remaining) >= needed:
                selected.extend(random.sample(remaining, needed))
            else:
                # Fill with any available
                available = [n for n in range(1, 26) if n not in selected]
                if available:
                    selected.extend(random.sample(available, min(needed, len(available))))

            if len(selected) == 14:
                candidates.append(sorted(selected))

        return candidates

    def strategy_6_genetic_algorithm(self, count=5000, generations=10):
        """Strategy 6: Genetic algorithm evolution"""
        print("\n[6/10] Genetic Algorithm Evolution...")

        # Initial population
        population = [sorted(random.sample(range(1, 26), 14)) for _ in range(200)]

        for gen in range(generations):
            # Evaluate fitness
            fitness_scores = [(comb, test_combination_fast(comb, self.all_series_data, self.test_series_ids))
                             for comb in population]
            fitness_scores.sort(key=lambda x: x[1], reverse=True)

            # Keep top 50%
            survivors = [comb for comb, score in fitness_scores[:100]]

            # Breed new population
            new_population = survivors[:]
            while len(new_population) < 200:
                parent1, parent2 = random.sample(survivors, 2)

                # Crossover
                child = list(set(parent1[:7] + parent2[:7]))

                # Fill to 14
                available = [n for n in range(1, 26) if n not in child]
                if available:
                    needed = 14 - len(child)
                    child.extend(random.sample(available, min(needed, len(available))))

                # Mutation (10% chance)
                if random.random() < 0.1 and len(child) == 14:
                    idx = random.randint(0, 13)
                    new_num = random.randint(1, 25)
                    if new_num not in child:
                        child[idx] = new_num

                if len(child) == 14:
                    new_population.append(sorted(child))

            population = new_population[:200]

        # Return best evolved + variations
        final_fitness = [(comb, test_combination_fast(comb, self.all_series_data, self.test_series_ids))
                        for comb in population]
        final_fitness.sort(key=lambda x: x[1], reverse=True)

        result = [comb for comb, score in final_fitness]

        # Generate more variations of top performers
        while len(result) < count:
            base = random.choice(result[:20])
            variant = list(base)
            for _ in range(random.randint(1, 2)):
                idx = random.randint(0, 13)
                new_num = random.randint(1, 25)
                if new_num not in variant:
                    variant[idx] = new_num
            result.append(sorted(variant))

        return result[:count]

    def strategy_7_critical_forcing(self, count=5000):
        """Strategy 7: Force critical numbers"""
        print("\n[7/10] Critical Number Forcing...")
        candidates = []

        # If no critical numbers, use top frequency
        if not self.critical_numbers:
            self.critical_numbers = [num for num, freq in self.number_freq.most_common(15)]

        for _ in range(count):
            selected = []

            # Include 8-10 critical numbers
            if self.critical_numbers:
                num_critical = random.randint(8, min(10, len(self.critical_numbers)))
                selected.extend(random.sample(self.critical_numbers, num_critical))

            # Fill rest randomly
            available = [n for n in range(1, 26) if n not in selected]
            needed = 14 - len(selected)
            if len(available) >= needed:
                selected.extend(random.sample(available, needed))

            if len(selected) == 14:
                candidates.append(sorted(selected))

        return candidates

    def strategy_8_ensemble_ml(self, count=5000):
        """Strategy 8: Ensemble of multiple ML seeds"""
        print("\n[8/10] Ensemble ML (Multiple Seeds)...")
        candidates = []

        # Best seeds from previous testing
        best_seeds = [650, 950, 456, 4096, 555]
        predictions = []

        for seed in best_seeds:
            model = TrueLearningModel(seed=seed)
            for series_id in self.training_series_ids:
                events = self.all_series_data[str(series_id)]
                model.learn_from_series(series_id, events)
            pred = model.predict_best_combination(3151)
            predictions.append(pred)

        # Generate variations of ensemble predictions
        for _ in range(count):
            # Pick 2-3 predictions to blend
            selected_preds = random.sample(predictions, random.randint(2, 3))

            # Count number frequencies
            num_freq = Counter()
            for pred in selected_preds:
                for num in pred:
                    num_freq[num] += 1

            # Take top 14
            top_nums = [num for num, freq in num_freq.most_common(14)]

            # If not enough, fill randomly
            if len(top_nums) < 14:
                available = [n for n in range(1, 26) if n not in top_nums]
                needed = 14 - len(top_nums)
                top_nums.extend(random.sample(available, needed))

            candidates.append(sorted(top_nums[:14]))

        return candidates

    def strategy_9_pattern_filtering(self, count=5000):
        """Strategy 9: Pattern-based filtering"""
        print("\n[9/10] Pattern-Based Filtering...")
        candidates = []

        # If no critical numbers, use top frequency
        if not self.critical_numbers:
            self.critical_numbers = [num for num, freq in self.number_freq.most_common(15)]

        # Analyze patterns in historical data
        # Pattern 1: Distribution across columns
        col0_avg = sum(1 for num in self.critical_numbers if 1 <= num <= 9) / len(self.critical_numbers) if self.critical_numbers else 0.36
        col1_avg = sum(1 for num in self.critical_numbers if 10 <= num <= 19) / len(self.critical_numbers) if self.critical_numbers else 0.40
        col2_avg = sum(1 for num in self.critical_numbers if 20 <= num <= 25) / len(self.critical_numbers) if self.critical_numbers else 0.24

        attempts = 0
        max_attempts = count * 10

        while len(candidates) < count and attempts < max_attempts:
            attempts += 1
            combo = sorted(random.sample(range(1, 26), 14))

            # Check distribution
            col0_count = sum(1 for num in combo if 1 <= num <= 9)
            col1_count = sum(1 for num in combo if 10 <= num <= 19)
            col2_count = sum(1 for num in combo if 20 <= num <= 25)

            # Should roughly match historical distribution
            if 4 <= col0_count <= 7 and 4 <= col1_count <= 7 and 1 <= col2_count <= 4:
                # Check critical number inclusion (at least 50%)
                critical_count = sum(1 for num in combo if num in self.critical_numbers)
                if critical_count >= len(self.critical_numbers) * 0.5:
                    candidates.append(combo)

        # Fill rest if needed
        while len(candidates) < count:
            candidates.append(sorted(random.sample(range(1, 26), 14)))

        return candidates[:count]

    def strategy_10_hybrid_super(self, count=5000):
        """Strategy 10: Hybrid super-strategy (best of all)"""
        print("\n[10/10] Hybrid Super-Strategy...")
        candidates = []

        # Ensure we have critical and hot numbers
        if not self.critical_numbers:
            self.critical_numbers = [num for num, freq in self.number_freq.most_common(15)]
        if not self.hot_numbers:
            self.hot_numbers = [num for num, freq in self.number_freq.most_common(12)]

        # Combine best elements from all strategies
        for _ in range(count):
            selected = set()

            # 1. Start with critical numbers (6-8)
            if self.critical_numbers:
                num_critical = random.randint(6, min(8, len(self.critical_numbers)))
                selected.update(random.sample(self.critical_numbers, num_critical))

            # 2. Add hot numbers (2-3)
            hot_available = [h for h in self.hot_numbers if h not in selected]
            if hot_available:
                num_hot = random.randint(2, min(3, len(hot_available)))
                selected.update(random.sample(hot_available, num_hot))

            # 3. Add one number with strong pair affinity
            if selected:
                candidates_scores = []
                for num in range(1, 26):
                    if num not in selected:
                        score = sum(self.pair_freq[tuple(sorted([num, s]))] for s in selected)
                        candidates_scores.append((num, score))

                if candidates_scores:
                    best_pair_num = max(candidates_scores, key=lambda x: x[1])[0]
                    selected.add(best_pair_num)

            # 4. Fill rest with frequency-weighted selection
            while len(selected) < 14:
                available = [n for n in range(1, 26) if n not in selected]
                if not available:
                    break

                weights = [self.number_freq[n] for n in available]
                if sum(weights) > 0:
                    choice = random.choices(available, weights=weights)[0]
                    selected.add(choice)
                else:
                    selected.add(random.choice(available))

            if len(selected) == 14:
                candidates.append(sorted(list(selected)))

        return candidates


print("=" * 80)
print("COMPREHENSIVE LOTTERY PREDICTION STUDY")
print("Testing 10 Advanced Strategies to Surpass 72.79% Baseline")
print("=" * 80)
print()

# Load data
all_series_data = load_series_data()
all_series_ids = sorted([int(sid) for sid in all_series_data.keys()])

# Test range
test_series_ids = [sid for sid in all_series_ids if sid >= 3130 and sid <= 3150]
training_series_ids = [sid for sid in all_series_ids if sid < 3130]

print(f"Training: {len(training_series_ids)} series ({min(training_series_ids)}-{max(training_series_ids)})")
print(f"Testing: {len(test_series_ids)} series ({min(test_series_ids)}-{max(test_series_ids)})")
print()

# Initialize tester
tester = AdvancedStrategyTester(all_series_data, test_series_ids, training_series_ids)
print()

# Test all strategies
strategies = [
    ("Pure Random", tester.strategy_1_pure_random),
    ("Mandel-Style", tester.strategy_2_mandel_style),
    ("Frequency-Weighted", tester.strategy_3_frequency_weighted),
    ("Pair Affinity", tester.strategy_4_pair_affinity),
    ("Hot/Cold Balance", tester.strategy_5_hot_cold),
    ("Genetic Algorithm", tester.strategy_6_genetic_algorithm),
    ("Critical Forcing", tester.strategy_7_critical_forcing),
    ("Ensemble ML", tester.strategy_8_ensemble_ml),
    ("Pattern Filtering", tester.strategy_9_pattern_filtering),
    ("Hybrid Super", tester.strategy_10_hybrid_super)
]

results = {}

print("=" * 80)
print("TESTING ALL STRATEGIES")
print("=" * 80)

for name, strategy_func in strategies:
    print(f"\nTesting: {name}...")
    random.seed(650)  # Reproducibility

    candidates = strategy_func(count=5000)

    # Remove duplicates
    unique = list({tuple(c): c for c in candidates}.values())
    print(f"  Generated {len(unique)} unique combinations")

    # Test all
    print(f"  Testing...")
    scores = []
    for combo in unique:
        score = test_combination_fast(combo, all_series_data, test_series_ids)
        scores.append((combo, score * 100 / 14))

    scores.sort(key=lambda x: x[1], reverse=True)

    # Statistics
    best_score = scores[0][1]
    avg_score = sum(s[1] for s in scores) / len(scores)
    top10_avg = sum(s[1] for s in scores[:10]) / 10

    results[name] = {
        'best': best_score,
        'average': avg_score,
        'top10_avg': top10_avg,
        'best_combo': scores[0][0],
        'all_scores': scores
    }

    print(f"  ✅ Best: {best_score:.2f}% | Avg: {avg_score:.2f}% | Top10 Avg: {top10_avg:.2f}%")

print()
print("=" * 80)
print("RESULTS SUMMARY")
print("=" * 80)
print()

# Sort by best score
sorted_results = sorted(results.items(), key=lambda x: x[1]['best'], reverse=True)

print(f"{'Rank':<5} {'Strategy':<25} {'Best':<10} {'Avg':<10} {'Top10':<10} {'vs Baseline':<12}")
print("-" * 80)

baseline_score = 72.79  # Mandel-style baseline

for rank, (name, data) in enumerate(sorted_results, 1):
    marker = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else f"{rank}."
    diff = data['best'] - baseline_score
    diff_str = f"{diff:+.2f}%"

    print(f"{marker:<5} {name:<25} {data['best']:<10.2f} {data['average']:<10.2f} {data['top10_avg']:<10.2f} {diff_str:<12}")

print()

# Best overall
winner_name, winner_data = sorted_results[0]
print(f"🏆 WINNER: {winner_name}")
print(f"   Best Score: {winner_data['best']:.2f}%")
print(f"   Improvement over baseline: {winner_data['best'] - baseline_score:+.2f}%")
print()

# Save detailed results
output = {
    "test_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "baseline_score": baseline_score,
    "strategies_tested": len(strategies),
    "test_series": test_series_ids,
    "results": {
        name: {
            "best_score": round(data['best'], 2),
            "average_score": round(data['average'], 2),
            "top10_average": round(data['top10_avg'], 2),
            "vs_baseline": round(data['best'] - baseline_score, 2),
            "best_combination": data['best_combo'],
            "top_10_combinations": [combo for combo, score in data['all_scores'][:10]]
        }
        for name, data in results.items()
    },
    "winner": {
        "strategy": winner_name,
        "score": round(winner_data['best'], 2),
        "improvement": round(winner_data['best'] - baseline_score, 2),
        "combination": winner_data['best_combo']
    }
}

with open('comprehensive_strategy_study.json', 'w') as f:
    json.dump(output, f, indent=2)

print("📁 Results saved to: comprehensive_strategy_study.json")
print()
print("✅ COMPREHENSIVE STUDY COMPLETE!")
