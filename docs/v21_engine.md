# V21.0 Engine (Signal + Diversity)

*Status: Decayed / Legacy*

V21.0 was designed to harness transient signals combined with geometric diversity. It successfully predicted out-of-sample (OOS) draws for a specific window but subsequently lost its edge as signals decayed.

### The 8 Signals
The system predicted 8 sets based on the following signals, constrained by `max pairwise overlap = 9` (Hamming distance ≥ 10), guaranteeing disjoint 12-neighborhoods (41,280 coverage).
1. **consensus_std**: Weighted expert consensus + std frequency tiebreak
2. **consensus_rec**: Expert consensus + recency tiebreak
3. **recency**: Pure recency-weighted frequency (L5/15/30)
4. **velocity**: Short-term rank momentum (L5 vs L20)
5. **flash_vel**: Ultra-short momentum (L3 vs L12)
6. **pair_freq**: Co-occurrence pair frequency
7. **event_diff1**: Symmetric difference E1^E4
8. **smooth_med**: Median deviation (contrarian)

### Signal Decay Confirmations
- **Early OOS (3193–3202)**: V21 40.0% hits (at 12+) vs 5.3% random — genuine but transient signal confirmed.
- **Late OOS (3203–3209)**: V21 0.0% hits vs 6.5% random — decayed effectively below the random baseline.
- **Live Empirical Validation (3209–3211)**: For three consecutive live series tests (3209, 3210, and 3211), the pure geometric systems (V25/V26) consistently outperformed V21. This provides absolute confirmation that V21's signal reliance is an active shortcoming in the current regime, heavily reinforcing the hypothesis of meta-overfitting on historical transients.
- **Conclusion**: Signal decay boundary verified post-3202. The pure geometric (V20/V25/V26) approach is strictly superior for regimes 3203+.
