"""Operating characteristics of the pre-registered Spearman verdict rule.

Answers the Gate 1 deferred question "can Spearman discriminate 0.9 from 0.7
at the expected N" with a measurement instead of a qualitative caveat.

Deterministic: every simulation uses a fixed seed. The tables this script
prints are quoted in the 2026-08-19 amendment to the pre-registration; CI
re-runs it and diffs the output against the committed reference
(tools/stats/rs_power_expected.txt), the same pinning discipline as
check_claims.py. Runtime is about 25 seconds (measured; the draft of this docstring
said "a few minutes").

Method notes
------------
- CI formula: Bonett & Wright (2000) — Fisher z with SE sqrt((1 + r^2/2)/(n-3)).
- To simulate a target Spearman rho_s with Gaussian copulas, the Pearson
  correlation is set via rho_p = 2*sin(pi*rho_s/6) (Pearson 1907 relation).
- Clustered design: blot-level and ratio-level components mixed with weight
  ICC, mimicking "ratios within a blot are not independent" from
  DECISION_unit_of_analysis.md. Effective N is recovered by inverting the
  Bonett-Wright SE from the observed sd of z(r_s).

Usage: python -m tools.stats.rs_power
"""

from __future__ import annotations

import numpy as np
from scipy.stats import spearmanr

SEED = 20260819
REPS_INDEP = 4000
REPS_CLUST = 3000
REPS_BIAS = 6000


def z(r: np.ndarray | float) -> np.ndarray | float:
    return np.arctanh(np.clip(r, -0.999999, 0.999999))


def iz(v: np.ndarray | float) -> np.ndarray | float:
    return np.tanh(v)


def bw_ci(rs: float, n: int, conf: float = 1.96) -> tuple[float, float]:
    """Bonett-Wright 95% CI for a Spearman correlation."""
    se = np.sqrt((1.0 + rs**2 / 2.0) / (n - 3))
    return float(iz(z(rs) - conf * se)), float(iz(z(rs) + conf * se))


def _chol(rho_s: float) -> np.ndarray:
    rho_p = 2 * np.sin(np.pi * rho_s / 6)
    return np.linalg.cholesky(np.array([[1.0, rho_p], [rho_p, 1.0]]))


def table_a() -> None:
    print("=== A. Bonett-Wright 95% CI for an OBSERVED r_s = 0.90 ===")
    print(f"{'N':>4} {'lower':>7} {'upper':>7}  {'lower > 0.70?':>13}")
    for n in [8, 10, 12, 15, 20, 25, 30, 35, 40, 50, 60]:
        lo, hi = bw_ci(0.90, n)
        print(f"{n:>4} {lo:>7.3f} {hi:>7.3f}  {'yes' if lo > 0.70 else 'no':>13}")


def table_b() -> None:
    print("\n=== B. Smallest N at which an observed r_s excludes 0.70 ===")
    for rs in [0.85, 0.90, 0.92, 0.95, 0.97]:
        n = 6
        while n < 400 and bw_ci(rs, n)[0] <= 0.70:
            n += 1
        print(f"  observed r_s = {rs:.2f}  ->  N >= {n}")


def table_c(rng: np.random.Generator) -> None:
    print("\n=== C. Monte Carlo, independent observations, true rho_s = 0.90 ===")
    L = _chol(0.90)
    for n in [10, 15, 20, 30]:
        rs = np.empty(REPS_INDEP)
        excl = 0
        for i in range(REPS_INDEP):
            e = rng.standard_normal((n, 2)) @ L.T
            r = spearmanr(e[:, 0], e[:, 1]).statistic
            rs[i] = r
            if bw_ci(r, n)[0] > 0.70:
                excl += 1
        analytic = np.sqrt((1 + 0.81 / 2) / (n - 3))
        print(
            f"  N={n:>3}  mean r_s {rs.mean():.3f}  sd(z) {np.std(z(rs)):.3f} "
            f"(analytic {analytic:.3f})  P(CI excludes 0.70) = {excl / REPS_INDEP:.2f}"
        )


def table_d(rng: np.random.Generator) -> None:
    print("\n=== D. Clustered: B blots x m ratios, within-blot ICC, true rho_s = 0.90 ===")
    print(f"{'design':>22} {'N':>4} {'sd(z) obs':>10} {'N_eff':>7} {'P(CI excl 0.70)':>16}")
    L = _chol(0.90)
    for B, m, icc in [
        (10, 3, 0.0), (10, 3, 0.3), (10, 3, 0.5), (10, 3, 0.7),
        (8, 4, 0.5), (12, 3, 0.5), (6, 3, 0.5),
    ]:
        n = B * m
        zs = np.empty(REPS_CLUST)
        excl = 0
        for i in range(REPS_CLUST):
            U = rng.standard_normal((B, 2)) @ L.T
            E = rng.standard_normal((B, m, 2)) @ L.T
            X = np.sqrt(icc) * U[:, None, 0] + np.sqrt(1 - icc) * E[:, :, 0]
            Y = np.sqrt(icc) * U[:, None, 1] + np.sqrt(1 - icc) * E[:, :, 1]
            r = spearmanr(X.ravel(), Y.ravel()).statistic
            zs[i] = z(r)
            if bw_ci(r, n)[0] > 0.70:
                excl += 1
        sd = float(np.std(zs))
        n_eff = 3 + (1 + 0.81 / 2) / sd**2
        label = f"{B} blots x {m}, ICC {icc}"
        print(f"{label:>22} {n:>4} {sd:>10.3f} {n_eff:>7.1f} {excl / REPS_CLUST:>16.2f}")


def table_e(rng: np.random.Generator) -> None:
    print("\n=== E. Point-estimate verdict rule vs known truth ===")

    def sim(n: int, rho_s: float) -> np.ndarray:
        L = _chol(rho_s)
        out = np.empty(REPS_BIAS)
        for i in range(REPS_BIAS):
            e = rng.standard_normal((n, 2)) @ L.T
            out[i] = spearmanr(e[:, 0], e[:, 1]).statistic
        return out

    print("P(observed >= 0.90 | true rho_s), i.e. rate of declaring agreement:")
    truths = [0.60, 0.70, 0.80, 0.85, 0.90, 0.95]
    print(f"{'N':>4} | " + " ".join(f"true {t:.2f}".rjust(10) for t in truths))
    for n in [10, 15, 20, 30, 40]:
        row = [(sim(n, t) >= 0.90).mean() for t in truths]
        print(f"{n:>4} | " + " ".join(f"{v:>10.3f}" for v in row))
    print(
        "\nReading: the rule is conservative. It essentially never declares"
        "\nagreement when true rho_s <= 0.70, rarely at 0.80, and declares it"
        "\nonly ~40% of the time even when agreement (0.90) is exactly true,"
        "\nbecause small-N Spearman is biased low. The error direction is the"
        "\nsafe one for this project: false modesty, not false confidence."
    )


def main() -> None:
    rng = np.random.default_rng(SEED)
    print(f"seed = {SEED}")
    table_a()
    table_b()
    table_c(rng)
    table_d(rng)
    table_e(rng)


if __name__ == "__main__":
    main()
