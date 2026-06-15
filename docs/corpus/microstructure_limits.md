# Stylized Microstructure Limits

Egress uses a stylized persistent order book, not observed Level 2 depth. Resting
orders can age, cancel, and replenish under stress, and the matching engine sets
prices from submitted orders. That makes the path internally consistent for a
scenario simulation, but it is not a calibrated prediction of exact intraday
slippage, queue position, hidden liquidity, dark-pool fills, or block facilitation.

Analyst language should say "under this scenario" and "assumption-based stress
range." It should not call outputs exact forecasts or exact causal proof.
