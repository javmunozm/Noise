#!/usr/bin/env python3
"""
Jackpot Simulation - Can TrueLearningModel achieve 14/14?
Test across 23 series with 100 seeds each = 2,300 predictions
"""

import sys
sys.path.insert(0, '/home/user/Random/python_ml')

from true_learning_model_port import TrueLearningModel
import json
from collections import Counter

def load_data():
    with open('all_series_data.json', 'r') as f:
        data = json.load(f)
    data['3152'] = [
        [1, 4, 5, 6, 8, 9, 10, 12, 13, 18, 21, 23, 24, 25],
        [1, 4, 5, 8, 10, 13, 15, 17, 18, 20, 21, 23, 24, 25],
        [1, 3, 6, 7, 12, 13, 14, 15, 16, 19, 20, 21, 22, 25],
        [2, 4, 5, 6, 7, 9, 10, 14, 16, 17, 20, 22, 23, 24],
        [1, 2, 4, 8, 10, 11, 15, 16, 17, 19, 20, 21, 23, 25],
        [2, 3, 4, 5, 6, 9, 10, 12, 17, 18, 20, 21, 23, 24],
        [1, 2, 4, 5, 6, 7, 8, 12, 17, 18, 22, 23, 24, 25]
    ]
    return data

def test_series(data, series_id, num_seeds=100):
    results = []
    for seed in range(num_seeds):
        model = TrueLearningModel(seed=seed)
        for train_id in range(2980, series_id):
            if str(train_id) in data:
                model.learn_from_series(train_id, data[str(train_id)])
        prediction = model.predict_best_combination(series_id)
        matches = [len(set(prediction) & set(event)) for event in data[str(series_id)]]
        peak = max(matches)
        results.append({'seed': seed, 'peak': peak, 'prediction': prediction, 'matches': matches})
    return results

data = load_data()
print("="*80)
print("JACKPOT SIMULATION - TrueLearningModel")
print("="*80)
print("\nTesting 23 series × 100 seeds = 2,300 predictions\n")

all_results = {}
best_overall = 0
best_info = None
jackpots = []

for series_id in range(3130, 3153):
    if str(series_id) not in data:
        continue
    print(f"Series {series_id}... ", end='', flush=True)
    results = test_series(data, series_id, 100)
    all_results[series_id] = results
    best = max(results, key=lambda x: x['peak'])
    avg = sum(r['peak'] for r in results) / len(results)
    print(f"Best: {best['peak']}/14, Avg: {avg:.2f}/14")
    
    if best['peak'] == 14:
        jackpots.append({'series': series_id, 'seed': best['seed']})
    if best['peak'] > best_overall:
        best_overall = best['peak']
        best_info = {'series': series_id, 'seed': best['seed'], 'peak': best['peak'], 'pred': best['prediction'], 'matches': best['matches']}

all_peaks = [r['peak'] for results in all_results.values() for r in results]

print("\n" + "="*80)
print("RESULTS")
print("="*80)
print(f"\nJackpots (14/14): {len(jackpots)}")
print(f"Best achieved: {best_overall}/14 (Series {best_info['series']}, Seed {best_info['seed']})")
print(f"Gap to jackpot: {14 - best_overall} numbers\n")
print(f"Mean: {sum(all_peaks)/len(all_peaks):.2f}/14")
print(f"12+: {len([p for p in all_peaks if p >= 12])}/{len(all_peaks)} ({len([p for p in all_peaks if p >= 12])/len(all_peaks)*100:.2f}%)")
print(f"11+: {len([p for p in all_peaks if p >= 11])}/{len(all_peaks)} ({len([p for p in all_peaks if p >= 11])/len(all_peaks)*100:.2f}%)")
print(f"10+: {len([p for p in all_peaks if p >= 10])}/{len(all_peaks)} ({len([p for p in all_peaks if p >= 10])/len(all_peaks)*100:.2f}%)")

if not jackpots:
    print(f"\n❌ NO JACKPOTS in {len(all_peaks)} attempts")
    print("   ML optimizes for average, not jackpot")

with open('jackpot_simulation_results.json', 'w') as f:
    json.dump({'jackpots': len(jackpots), 'best': best_overall, 'mean': sum(all_peaks)/len(all_peaks), 'best_info': best_info}, f, indent=2)
print(f"\n📁 Saved: jackpot_simulation_results.json")
