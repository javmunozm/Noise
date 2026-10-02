#!/usr/bin/env python3
"""
TrueLearningModel - Python Port (Phase 1 RESTORED)

Port of the .NET TrueLearningModel to Python for rapid testing.
Matches Phase 1 Pure implementation after Phase 2 revert.

Features:
- Multi-event learning (ALL 7 events per series)
- Importance-weighted learning (1.15x to 1.60x)
- Pair/triplet affinity tracking
- Critical number identification (5+ events)
- Hybrid cold/hot number selection
- Always learns (no threshold)

Performance Target: 71.4% baseline
"""

import random
import json
from typing import List, Dict, Tuple, Set
from collections import defaultdict
from datetime import datetime


class TrueLearningModel:
    """Phase 1 Pure - Proven 71.4% baseline"""

    # System constants
    MIN_NUMBER = 1
    MAX_NUMBER = 25
    NUMBERS_PER_COMBINATION = 14
    UNIQUENESS_LOOKBACK = 151

    # Hybrid balanced strategy (MATCHED TO C# Nov 17, 2025)
    RECENT_SERIES_LOOKBACK = 16  # MATCHED TO C# - C# uses 16-series lookback for cold/hot calculation
    COLD_NUMBER_COUNT = 7
    HOT_NUMBER_COUNT = 7

    # Learning rates
    LEARNING_RATE_STRONG_BOOST = 3.0
    LEARNING_RATE_MEDIUM_BOOST = 2.0
    LEARNING_RATE_BASE = 0.8

    # Importance multipliers
    IMPORTANCE_HIGH = 1.50
    IMPORTANCE_MEDIUM = 1.35
    IMPORTANCE_LOW = 1.20
    IMPORTANCE_CRITICAL = 1.60

    # Penalties
    PENALTY_HIGH_FREQUENCY = 0.75
    PENALTY_MEDIUM_FREQUENCY = 0.85
    PENALTY_LOW_FREQUENCY = 0.92

    # Affinity multipliers
    PAIR_AFFINITY_MULTIPLIER = 25.0
    TRIPLET_AFFINITY_MULTIPLIER = 35.0
    CRITICAL_NUMBER_GENERATION_BOOST = 5.0

    # Candidate pool (MATCHED TO C# Nov 17, 2025)
    CANDIDATE_POOL_SIZE = 10000  # Generate 10,000 candidates
    CANDIDATES_TO_SCORE = 1000   # Score top 1,000 (C# matches this)

    # Pattern weights
    PATTERN_WEIGHT_CONSECUTIVE = 0.3
    PATTERN_WEIGHT_SUM_RANGE = 0.3
    PATTERN_WEIGHT_DISTRIBUTION = 0.2
    PATTERN_WEIGHT_HIGH_NUMBERS = 0.2

    def __init__(self, seed: int = None, pool_size: int = None, cold_hot_boost: float = None):
        """
        Initialize TrueLearningModel

        Args:
            seed: Random seed for reproducibility (default: None)
            pool_size: Candidate pool size (default: CANDIDATE_POOL_SIZE constant)
            cold_hot_boost: Cold/hot number boost multiplier (default: 50.0)
        """
        # Set seed if provided
        if seed is not None:
            random.seed(seed)
            self._seed = seed
        else:
            self._seed = None

        # Override pool size if provided
        if pool_size is not None:
            self.CANDIDATE_POOL_SIZE = pool_size

        # Store cold/hot boost (MATCHED TO C# Nov 17, 2025)
        # C# uses 50.0x boost for both cold and hot numbers
        # This is the CRITICAL parameter that drives hybrid selection
        self._cold_hot_boost = cold_hot_boost if cold_hot_boost is not None else 50.0

        self.number_frequency_weights = {i: 1.0 for i in range(self.MIN_NUMBER, self.MAX_NUMBER + 1)}
        self.position_weights = {i: 1.0 for i in range(self.MIN_NUMBER, self.MAX_NUMBER + 1)}
        self.pattern_weights = {
            'consecutive': self.PATTERN_WEIGHT_CONSECUTIVE,
            'sum_range': self.PATTERN_WEIGHT_SUM_RANGE,
            'distribution': self.PATTERN_WEIGHT_DISTRIBUTION,
            'high_numbers': self.PATTERN_WEIGHT_HIGH_NUMBERS,
        }
        self.learning_rate = 0.1

        self.training_data = []
        self.pair_affinities = {}
        self.triplet_affinities = {}
        self.number_avoidance = defaultdict(lambda: defaultdict(int))
        self.recent_critical_numbers = set()
        self.temporal_weights = {}

        self.hybrid_cold_numbers = set()
        self.hybrid_hot_numbers = set()
        self.recent_frequency_map = {}

        self.uniqueness_history = []

        # Iteration counter for weight decay (FIX #4)
        self._validation_counter = 0

    def learn_from_series(self, series_id: int, combinations: List[List[int]]):
        """Learn from a complete series (7 events)"""
        self.training_data.append({
            'series_id': series_id,
            'combinations': combinations
        })
        self._update_weights(series_id, combinations)

        # Add to uniqueness history
        for combo in combinations:
            self.uniqueness_history.append(sorted(combo))
            if len(self.uniqueness_history) > self.UNIQUENESS_LOOKBACK * 7:
                self.uniqueness_history.pop(0)

    def _update_weights(self, series_id: int, combinations: List[List[int]]):
        """Update weights based on series patterns"""
        # Calculate hybrid cold/hot numbers from recent series
        if len(self.training_data) >= self.RECENT_SERIES_LOOKBACK:
            self.recent_frequency_map = {}
            recent_series = sorted(self.training_data, key=lambda x: x['series_id'], reverse=True)[:self.RECENT_SERIES_LOOKBACK]

            for series in recent_series:
                for combo in series['combinations']:
                    for num in combo:
                        self.recent_frequency_map[num] = self.recent_frequency_map.get(num, 0) + 1

            # Identify cold and hot numbers
            sorted_freq = sorted(self.recent_frequency_map.items(), key=lambda x: x[1])
            self.hybrid_cold_numbers = set([num for num, _ in sorted_freq[:self.COLD_NUMBER_COUNT]])
            self.hybrid_hot_numbers = set([num for num, _ in sorted_freq[-self.HOT_NUMBER_COUNT:]])

        # Analyze cross-event patterns
        all_numbers_in_series = defaultdict(int)
        event_count = len(combinations)

        for combo in combinations:
            for num in combo:
                all_numbers_in_series[num] += 1

        # Learn from highly frequent numbers
        for num, freq in all_numbers_in_series.items():
            frequency = freq / event_count
            if frequency >= 0.7:  # 70%+ of events
                self.number_frequency_weights[num] += self.learning_rate * 2.0
            elif frequency >= 0.5:  # 50%+ of events
                self.number_frequency_weights[num] += self.learning_rate * 1.5
            else:
                self.number_frequency_weights[num] += self.learning_rate * 0.5

        # Learn pair and triplet affinities from each event
        for combo in combinations:
            self._learn_pair_affinities(combo)
            self._learn_triplet_affinities(combo)

    def validate_and_learn(self, series_id: int, prediction: List[int], actual_results: List[List[int]]):
        """Validate prediction and learn from actual results"""
        # Find best match
        best_match = max(actual_results, key=lambda actual: len(set(prediction) & set(actual)))
        accuracy = len(set(prediction) & set(best_match)) / 14.0

        print(f"\n📊 Learning from Series {series_id}: Accuracy = {accuracy:.1%} ({len(set(prediction) & set(best_match))}/14)")

        # Analyze frequency across ALL 7 events
        number_frequency_in_series = defaultdict(int)
        for actual_event in actual_results:
            for num in actual_event:
                number_frequency_in_series[num] += 1

        # Identify critical numbers (5+ events)
        critical_numbers = [num for num, freq in number_frequency_in_series.items() if freq >= 5]
        critical_hit = [num for num in critical_numbers if num in prediction]
        critical_missed = [num for num in critical_numbers if num not in prediction]

        # MATCHED TO C#: Clear and replace critical numbers each iteration
        # C# does: recentCriticalNumbers.Clear() then adds current critical numbers
        # This is reactive learning - focuses only on most recent patterns
        self.recent_critical_numbers.clear()
        for cn in critical_numbers:
            self.recent_critical_numbers.add(cn)

        print(f"🔥 Critical numbers (5+ events): {' '.join(f'{n:02d}' for n in sorted(critical_numbers))}")
        print(f"   ✅ Hit: {len(critical_hit)}/{len(critical_numbers)} - {' '.join(f'{n:02d}' for n in sorted(critical_hit))}")
        print(f"   ❌ Missed: {len(critical_missed)}/{len(critical_numbers)} - {' '.join(f'{n:02d}' for n in sorted(critical_missed))}")

        # Average accuracy across all events
        avg_accuracy = sum(len(set(prediction) & set(actual)) / 14.0 for actual in actual_results) / len(actual_results)
        print(f"   Best: {accuracy:.1%}, Avg across 7 events: {avg_accuracy:.1%}")

        # Update temporal weights
        for num, freq in number_frequency_in_series.items():
            self.temporal_weights[num] = 1.0 + (freq / 7.0) * 0.5

        # Importance-weighted learning from ALL events
        for actual_event in actual_results:
            matches = set(prediction) & set(actual_event)
            missed = set(actual_event) - set(prediction)
            wrong = set(prediction) - set(actual_event)

            for num in missed:
                event_frequency = number_frequency_in_series[num]
                importance_multiplier = self.IMPORTANCE_LOW + ((self.IMPORTANCE_HIGH - self.IMPORTANCE_LOW) * (event_frequency / 7.0))
                self.number_frequency_weights[num] *= importance_multiplier

            for num in wrong:
                event_frequency = number_frequency_in_series[num]
                penalty_multiplier = self.PENALTY_HIGH_FREQUENCY + ((self.PENALTY_LOW_FREQUENCY - self.PENALTY_HIGH_FREQUENCY) * (event_frequency / 7.0))
                self.number_frequency_weights[num] *= penalty_multiplier

            for num in matches:
                self.number_frequency_weights[num] *= 1.05

            # Learn affinities
            self._learn_pair_affinities(list(actual_event))
            self._learn_triplet_affinities(list(actual_event))

        # Extra boost for critical numbers missed
        for critical_num in critical_missed:
            self.number_frequency_weights[critical_num] *= self.IMPORTANCE_CRITICAL
            print(f"   ⚠️  CRITICAL MISS: #{critical_num:02d} (appeared {number_frequency_in_series[critical_num]}/7 events) - HEAVY boost")

        # Learn from actual results
        self.learn_from_series(series_id, actual_results)

        # MATCHED TO C#: No weight normalization (C# doesn't normalize)
        # self._normalize_weights()  # DISABLED - Not in C# implementation

        # MATCHED TO C#: No weight decay (C# doesn't decay)
        # self._validation_counter += 1
        # if self._validation_counter % 10 == 0:
        #     self.apply_weight_decay(decay_rate=0.999)
        #     print(f"   🔄 Weight decay applied (iteration {self._validation_counter})")

        # Display top weights
        top_weights = sorted(self.number_frequency_weights.items(), key=lambda x: x[1], reverse=True)[:8]
        print(f"✅ Top 8 weights after learning: {' '.join(f'{n:02d}' for n, _ in top_weights)}")

        if self.pair_affinities:
            top_pairs = sorted(self.pair_affinities.items(), key=lambda x: x[1], reverse=True)[:3]
            print(f"🔗 Top 3 pair affinities: {', '.join(f'{a:02d}+{b:02d}' for (a, b), _ in top_pairs)}")

    def _learn_pair_affinities(self, combination: List[int]):
        """Learn which numbers appear together"""
        for i in range(len(combination)):
            for j in range(i + 1, len(combination)):
                pair = tuple(sorted([combination[i], combination[j]]))
                self.pair_affinities[pair] = self.pair_affinities.get(pair, 0.0) + self.learning_rate

    def _learn_triplet_affinities(self, combination: List[int]):
        """Learn 3-number patterns"""
        for i in range(len(combination)):
            for j in range(i + 1, len(combination)):
                for k in range(j + 1, len(combination)):
                    triplet = tuple(sorted([combination[i], combination[j], combination[k]]))
                    self.triplet_affinities[triplet] = self.triplet_affinities.get(triplet, 0.0) + self.learning_rate

    def _normalize_weights(self, max_weight: float = 100.0):
        """
        Normalize weights to prevent explosion (FIX #1 - Critical Bug Fix)

        Prevents weight explosion by scaling all weights down when maximum exceeds threshold.
        This maintains relative weight ratios while keeping values bounded.

        Args:
            max_weight: Maximum allowed weight value (default: 100.0)
        """
        # Normalize number frequency weights
        current_max = max(self.number_frequency_weights.values())
        if current_max > max_weight:
            normalization_factor = max_weight / current_max
            for num in self.number_frequency_weights:
                self.number_frequency_weights[num] *= normalization_factor

        # Normalize position weights
        if self.position_weights:
            pos_max = max(self.position_weights.values())
            if pos_max > max_weight:
                pos_factor = max_weight / pos_max
                for num in self.position_weights:
                    self.position_weights[num] *= pos_factor

    def apply_weight_decay(self, decay_rate: float = 0.999):
        """
        Apply exponential decay to learned weights (FIX #4 - Prevent Overfitting)

        This prevents old patterns from dominating new ones by gradually reducing
        all learned weights over time. Helps the model stay responsive to recent patterns.

        Args:
            decay_rate: Decay multiplier (default: 0.999)
                       - 0.999 means weights lose 0.1% per call
                       - After 100 calls: weight *= 0.999^100 ≈ 0.905 (90.5% retention)
                       - After 500 calls: weight *= 0.999^500 ≈ 0.606 (60.6% retention)

        Example:
            model.apply_weight_decay()  # Apply default 0.999 decay
            model.apply_weight_decay(0.995)  # Stronger decay (0.5% loss)
        """
        # Decay number frequency weights
        for num in self.number_frequency_weights:
            self.number_frequency_weights[num] *= decay_rate

        # Decay position weights
        for num in self.position_weights:
            self.position_weights[num] *= decay_rate

        # Decay pattern weights
        for pattern in self.pattern_weights:
            self.pattern_weights[pattern] *= decay_rate

        # Decay pair affinities
        for pair in self.pair_affinities:
            self.pair_affinities[pair] *= decay_rate

        # Decay triplet affinities
        for triplet in self.triplet_affinities:
            self.triplet_affinities[triplet] *= decay_rate

    def predict_best_combination(self, target_series_id: int) -> List[int]:
        """Generate best prediction for target series"""
        candidates = self._generate_candidates(target_series_id)
        scored = [(c, self._calculate_score(c)) for c in candidates]
        scored.sort(key=lambda x: x[1], reverse=True)

        return scored[0][0]

    def _generate_candidates(self, target_series_id: int) -> List[List[int]]:
        """Generate weighted candidate combinations"""
        candidates = []
        attempts = 0

        while len(candidates) < self.CANDIDATES_TO_SCORE and attempts < self.CANDIDATE_POOL_SIZE:
            candidate = self._generate_weighted_candidate()
            if self._is_valid_combination(candidate) and self._is_unique_combination(candidate, target_series_id):
                candidates.append(candidate)
            attempts += 1

        return candidates

    def _generate_weighted_candidate(self) -> List[int]:
        """Generate single weighted candidate"""
        numbers = []
        used = set()

        while len(numbers) < self.NUMBERS_PER_COMBINATION:
            num = self._select_weighted_number(used)
            if num not in used:
                numbers.append(num)
                used.add(num)

        return sorted(numbers)

    def _select_weighted_number(self, used: Set[int]) -> int:
        """Select number based on learned weights"""
        weights = {}

        for i in range(self.MIN_NUMBER, self.MAX_NUMBER + 1):
            if i not in used:
                weight = self.number_frequency_weights[i] * self.position_weights[i]

                # Hybrid balanced boost (configurable)
                if i in self.hybrid_cold_numbers or i in self.hybrid_hot_numbers:
                    weight *= self._cold_hot_boost

                # Critical number boost
                if i in self.recent_critical_numbers:
                    weight *= self.CRITICAL_NUMBER_GENERATION_BOOST

                # Temporal weight
                if i in self.temporal_weights:
                    weight *= self.temporal_weights[i]

                weights[i] = weight

        total_weight = sum(weights.values())
        rand_val = random.random() * total_weight

        current_weight = 0.0
        for num, weight in weights.items():
            current_weight += weight
            if current_weight >= rand_val:
                return num

        # Fallback
        available = [i for i in range(self.MIN_NUMBER, self.MAX_NUMBER + 1) if i not in used]
        return random.choice(available) if available else 1

    def _calculate_score(self, combination: List[int]) -> float:
        """Calculate score for combination"""
        score = 0.0

        # Frequency score
        for num in combination:
            score += self.number_frequency_weights[num]

        # Pattern scores
        consecutive_count = self._count_consecutive(combination)
        total_sum = sum(combination)
        distribution = self._calculate_distribution(combination)

        score += consecutive_count * self.pattern_weights['consecutive']

        # Sum range scoring (Phase 1 original: 160-240)
        if 160 <= total_sum <= 240:
            score += self.pattern_weights['sum_range']

        score += distribution * self.pattern_weights['distribution']

        # Pair affinity score
        score += self._calculate_pair_affinity_score(combination)

        # Triplet affinity score
        score += self._calculate_triplet_affinity_score(combination)

        # Critical number bonus
        critical_count = sum(1 for num in combination if num in self.recent_critical_numbers)
        score += critical_count * 10.0

        return score

    def _calculate_pair_affinity_score(self, combination: List[int]) -> float:
        """Calculate pair affinity score"""
        if not self.pair_affinities:
            return 0.0

        affinity_score = 0.0
        for i in range(len(combination)):
            for j in range(i + 1, len(combination)):
                pair = tuple(sorted([combination[i], combination[j]]))
                if pair in self.pair_affinities:
                    affinity_score += self.pair_affinities[pair]

        return affinity_score * self.PAIR_AFFINITY_MULTIPLIER

    def _calculate_triplet_affinity_score(self, combination: List[int]) -> float:
        """Calculate triplet affinity score"""
        if not self.triplet_affinities:
            return 0.0

        affinity_score = 0.0
        for i in range(len(combination)):
            for j in range(i + 1, len(combination)):
                for k in range(j + 1, len(combination)):
                    triplet = tuple(sorted([combination[i], combination[j], combination[k]]))
                    if triplet in self.triplet_affinities:
                        affinity_score += self.triplet_affinities[triplet]

        return affinity_score * self.TRIPLET_AFFINITY_MULTIPLIER

    def _count_consecutive(self, combination: List[int]) -> int:
        """Count consecutive number pairs"""
        count = 0
        for i in range(len(combination) - 1):
            if combination[i + 1] == combination[i] + 1:
                count += 1
        return count

    def _calculate_distribution(self, combination: List[int]) -> float:
        """Calculate distribution score"""
        low = sum(1 for n in combination if n <= 10)
        mid = sum(1 for n in combination if 11 <= n <= 20)
        high = sum(1 for n in combination if n >= 20)

        # Prefer balanced distribution
        return min(low, mid, high) / 14.0

    def _is_valid_combination(self, combination: List[int]) -> bool:
        """Check if combination is valid"""
        if len(combination) != self.NUMBERS_PER_COMBINATION:
            return False
        if len(set(combination)) != self.NUMBERS_PER_COMBINATION:
            return False
        if any(n < self.MIN_NUMBER or n > self.MAX_NUMBER for n in combination):
            return False
        return True

    def _is_unique_combination(self, combination: List[int], target_series_id: int) -> bool:
        """Check if combination is unique (not in recent history)"""
        sorted_combo = sorted(combination)
        return sorted_combo not in self.uniqueness_history

    def get_training_size(self) -> int:
        """Get number of series trained on"""
        return len(self.training_data)


def main():
    """Test the Python ML model"""
    print("=" * 80)
    print("TrueLearningModel - Python Port (Phase 1 RESTORED)")
    print("=" * 80)
    print()

    # Example usage
    model = TrueLearningModel()

    # Load some test data
    print("Model initialized successfully!")
    print(f"Candidate pool size: {model.CANDIDATE_POOL_SIZE}")
    print(f"Phase 1 features enabled: Multi-event, Pair affinity, Critical numbers")
    print()

    # Example: Add a series
    example_series = [
        [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
        [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
        [1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 24, 25],
        [2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 21, 22, 23, 24],
        [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
        [5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
        [1, 2, 3, 4, 5, 11, 12, 13, 14, 15, 21, 22, 23, 24],
    ]

    model.learn_from_series(3000, example_series)
    print(f"✅ Learned from example series 3000")
    print(f"✅ Training size: {model.get_training_size()} series")
    print()

    # Generate prediction
    prediction = model.predict_best_combination(3001)
    print(f"🎯 Prediction for Series 3001: {' '.join(f'{n:02d}' for n in prediction)}")


if __name__ == "__main__":
    main()
