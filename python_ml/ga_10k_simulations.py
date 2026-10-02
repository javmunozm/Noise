#!/usr/bin/env python3
"""
10,000 Genetic Algorithm Simulations
Validate GA performance across many runs with different random seeds
"""

import json
import random
from collections import defaultdict
from datetime import datetime
from true_learning_model import TrueLearningModel
import time


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


def genetic_algorithm_fast(all_series_data, test_series_ids, seed=None, generations=10, pop_size=200):
    """Fast genetic algorithm implementation"""
    if seed is not None:
        random.seed(seed)

    # Initial population
    population = [sorted(random.sample(range(1, 26), 14)) for _ in range(pop_size)]

    for gen in range(generations):
        # Evaluate fitness
        fitness_scores = [(combo, test_combination_fast(combo, all_series_data, test_series_ids))
                         for combo in population]
        fitness_scores.sort(key=lambda x: x[1], reverse=True)

        # Keep top 50%
        survivors = [combo for combo, score in fitness_scores[:pop_size//2]]

        # Breed new population
        new_population = survivors[:]
        while len(new_population) < pop_size:
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

        population = new_population[:pop_size]

    # Return best from final population
    final_fitness = [(combo, test_combination_fast(combo, all_series_data, test_series_ids))
                    for combo in population]
    best_combo, best_score = max(final_fitness, key=lambda x: x[1])

    return best_combo, best_score * 100 / 14  # Convert to percentage


print("=" * 80)
print("10,000 GENETIC ALGORITHM SIMULATIONS")
print("Validating GA Performance Across Multiple Runs")
print("=" * 80)
print()

# Load data
all_series_data = load_series_data()
all_series_ids = sorted([int(sid) for sid in all_series_data.keys()])

# Test on last 21 series
test_series_ids = [sid for sid in all_series_ids if sid >= 3130 and sid <= 3150]

print(f"Test Series: {len(test_series_ids)} series ({min(test_series_ids)}-{max(test_series_ids)})")
print()

# Run 10,000 simulations
num_simulations = 10000
results = []

print("Running simulations...")
print(f"Total: {num_simulations:,} simulations")
print()

start_time = time.time()
last_update = start_time

for sim_num in range(1, num_simulations + 1):
    # Use simulation number as seed for reproducibility
    best_combo, best_score = genetic_algorithm_fast(
        all_series_data,
        test_series_ids,
        seed=sim_num,
        generations=10,
        pop_size=200
    )

    results.append({
        'simulation': sim_num,
        'seed': sim_num,
        'combination': best_combo,
        'score': best_score
    })

    # Progress updates every 500 simulations or every 30 seconds
    current_time = time.time()
    if sim_num % 500 == 0 or (current_time - last_update) >= 30:
        elapsed = current_time - start_time
        rate = sim_num / elapsed if elapsed > 0 else 0
        eta_seconds = (num_simulations - sim_num) / rate if rate > 0 else 0
        eta_minutes = eta_seconds / 60

        # Current stats
        current_scores = [r['score'] for r in results]
        current_best = max(current_scores)
        current_avg = sum(current_scores) / len(current_scores)

        print(f"Progress: {sim_num:,}/{num_simulations:,} ({100*sim_num/num_simulations:.1f}%) | "
              f"Rate: {rate:.2f} sim/s | ETA: {eta_minutes:.1f} min | "
              f"Best: {current_best:.2f}% | Avg: {current_avg:.2f}%")

        last_update = current_time

# Final statistics
end_time = time.time()
total_time = end_time - start_time

print()
print("=" * 80)
print("SIMULATION COMPLETE")
print("=" * 80)
print()

# Extract scores
scores = [r['score'] for r in results]
scores.sort(reverse=True)

# Statistics
best_score = max(scores)
worst_score = min(scores)
mean_score = sum(scores) / len(scores)
median_score = scores[len(scores)//2]

# Percentiles
p95 = scores[int(len(scores) * 0.05)]
p75 = scores[int(len(scores) * 0.25)]
p25 = scores[int(len(scores) * 0.75)]
p5 = scores[int(len(scores) * 0.95)]

# Standard deviation
variance = sum((s - mean_score) ** 2 for s in scores) / len(scores)
std_dev = variance ** 0.5

print(f"Total Simulations: {num_simulations:,}")
print(f"Total Time: {total_time/60:.1f} minutes ({total_time:.0f} seconds)")
print(f"Average Rate: {num_simulations/total_time:.2f} simulations/second")
print()

print("=" * 80)
print("PERFORMANCE STATISTICS")
print("=" * 80)
print()

print(f"Best Score:     {best_score:.2f}%")
print(f"Mean Score:     {mean_score:.2f}%")
print(f"Median Score:   {median_score:.2f}%")
print(f"Worst Score:    {worst_score:.2f}%")
print(f"Std Deviation:  {std_dev:.2f}%")
print()

print("Percentiles:")
print(f"  95th (top 5%):     {p95:.2f}%")
print(f"  75th (top 25%):    {p75:.2f}%")
print(f"  50th (median):     {median_score:.2f}%")
print(f"  25th (bottom 25%): {p25:.2f}%")
print(f"  5th (bottom 5%):   {p5:.2f}%")
print()

# Performance categories
excellent = len([s for s in scores if s >= 72.0])
very_good = len([s for s in scores if 70.0 <= s < 72.0])
good = len([s for s in scores if 68.0 <= s < 70.0])
average = len([s for s in scores if 66.0 <= s < 68.0])
below = len([s for s in scores if s < 66.0])

print("Performance Distribution:")
print(f"  Excellent (≥72%):    {excellent:,} ({100*excellent/num_simulations:.1f}%)")
print(f"  Very Good (70-72%):  {very_good:,} ({100*very_good/num_simulations:.1f}%)")
print(f"  Good (68-70%):       {good:,} ({100*good/num_simulations:.1f}%)")
print(f"  Average (66-68%):    {average:,} ({100*average/num_simulations:.1f}%)")
print(f"  Below Average (<66%): {below:,} ({100*below/num_simulations:.1f}%)")
print()

# Top 10 results
print("=" * 80)
print("TOP 10 BEST RESULTS")
print("=" * 80)
print()

top_10_results = sorted(results, key=lambda x: x['score'], reverse=True)[:10]
for rank, result in enumerate(top_10_results, 1):
    combo_str = ' '.join(f"{n:02d}" for n in result['combination'])
    marker = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else f"{rank:2d}."
    print(f"{marker} Seed {result['seed']:>5} | {result['score']:5.2f}% | {combo_str}")

print()

# Confidence intervals
ci_95_lower = mean_score - 1.96 * std_dev / (num_simulations ** 0.5)
ci_95_upper = mean_score + 1.96 * std_dev / (num_simulations ** 0.5)

print("=" * 80)
print("STATISTICAL CONFIDENCE")
print("=" * 80)
print()

print(f"Mean Performance: {mean_score:.2f}%")
print(f"95% Confidence Interval: [{ci_95_lower:.2f}%, {ci_95_upper:.2f}%]")
print()

# Compare to baseline
baseline_score = 72.79
print(f"Original GA Result: {baseline_score:.2f}%")
print(f"Mean from 10K sims: {mean_score:.2f}%")
print(f"Best from 10K sims: {best_score:.2f}%")
print(f"Difference (best vs original): {best_score - baseline_score:+.2f}%")
print()

if best_score > baseline_score:
    print(f"✅ Found BETTER result! {best_score:.2f}% > {baseline_score:.2f}%")
elif best_score == baseline_score:
    print(f"✅ Matched original result: {best_score:.2f}%")
else:
    print(f"⚠️  Did not exceed original: {best_score:.2f}% vs {baseline_score:.2f}%")

print()

# Save detailed results
output = {
    "test_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "simulations": num_simulations,
    "total_time_seconds": round(total_time, 2),
    "test_series": test_series_ids,
    "statistics": {
        "best": round(best_score, 2),
        "mean": round(mean_score, 2),
        "median": round(median_score, 2),
        "worst": round(worst_score, 2),
        "std_dev": round(std_dev, 2),
        "percentile_95": round(p95, 2),
        "percentile_75": round(p75, 2),
        "percentile_25": round(p25, 2),
        "percentile_5": round(p5, 2),
        "ci_95_lower": round(ci_95_lower, 2),
        "ci_95_upper": round(ci_95_upper, 2)
    },
    "distribution": {
        "excellent_72plus": excellent,
        "very_good_70_72": very_good,
        "good_68_70": good,
        "average_66_68": average,
        "below_66": below
    },
    "top_10_seeds": [
        {
            "rank": rank,
            "seed": r['seed'],
            "score": round(r['score'], 2),
            "combination": r['combination']
        }
        for rank, r in enumerate(top_10_results, 1)
    ]
}

with open('ga_10k_simulations.json', 'w') as f:
    json.dump(output, f, indent=2)

print("=" * 80)
print(f"📁 Results saved to: ga_10k_simulations.json")
print("✅ 10,000 SIMULATIONS COMPLETE!")
print("=" * 80)
