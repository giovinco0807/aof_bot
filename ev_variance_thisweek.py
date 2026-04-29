import math

sd_per_hand = 3.73
bb = 2000.0

def normal_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))

print("=" * 70)
print("  THIS WEEK VARIANCE ANALYSIS (3/31 ~ 4/4 incl today)")
print("=" * 70)

cases = [
    ("13076903 thisweek (~7652h)", 6426 + 1226, -293.6 - 394.2, -26.6 - 132.3),
    ("13268363 thisweek (~4495h)", 4487 + 8, -165.0 - 10.0, -116.1 - 5.5),
    ("Combined thisweek (~12147h)", 6426 + 1226 + 4487 + 8, -293.6 - 394.2 - 165.0 - 10.0, -26.6 - 132.3 - 116.1 - 5.5),
]

print()
print("  DOWNSWING FREQUENCY")
print("-" * 70)

for label, nh, pl, ev in cases:
    diff = pl - ev
    sd_n = sd_per_hand * math.sqrt(nh)
    z = diff / sd_n if sd_n > 0 else 0
    p = normal_cdf(z)
    rb = nh * 3.1 / 100 * 0.7

    print(f"  {label}:")
    print(f"    P/L: {pl:>+.1f}BB  EV: {ev:>+.1f}BB")
    print(f"    Downswing: {diff:>+.1f}BB")
    print(f"    Expected SD: {sd_n:.0f}BB")
    print(f"    z-score: {z:+.2f}")
    print(f"    P(this bad or worse): {p:.1%}")
    if p < 0.5:
        freq = int(round(1 / p))
        print(f"    -> approx 1 in {freq}")
    print(f"    RB incl P/L: {pl + rb:>+.1f}BB  RB incl EV: {ev + rb:>+.1f}BB")
    print()

print("=" * 70)
print("  NORMAL VARIANCE RANGES")
print("-" * 70)
for nh in [5000, 7500, 10000, 12000]:
    sd_n = sd_per_hand * math.sqrt(nh)
    print(f"  {nh:>6,}h: 68%=+/-{sd_n:.0f}BB  95%=+/-{2 * sd_n:.0f}BB  99%=+/-{2.58 * sd_n:.0f}BB")
print("=" * 70)
