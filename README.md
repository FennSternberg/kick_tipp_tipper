# kick_tipp_tipper

Command line helper for optimizing Kicktipp-style World Cup score predictions
from correct-score betting odds.

The scoring model is:

- 4 points for the exact score.
- 3 points for the correct non-draw goal difference.
- 2 points for the correct result.
- 0 points otherwise.

## Setup

The default provider is Bovada's public football JSON feed, so no paid API key
is required. The tool uses only the Python standard library.

The optimizer needs an actual exact-score betting market. It does not estimate
or invent score probabilities from winner/total markets. If Bovada does
not expose an exact-score market and an any-other-score bucket for a fixture,
`expected-points` fails.

## Usage

Show upcoming fixtures in chronological order:

```powershell
python -m kick_tipp_tipper.cli fixtures
```

Rank score lines for a fixture:

```powershell
python -m kick_tipp_tipper.cli expected-points "England v Brazil" --top 10
```

You can also use a fixture id from the fixtures output:

```powershell
python -m kick_tipp_tipper.cli expected-points <fixture-id>
```

Or use the fixture number shown by `fixtures`; `1` is the next upcoming
fixture, `2` is the fixture after that, and so on:

```powershell
python -m kick_tipp_tipper.cli expected-points 1 --top 10
```

Optionally infer score lines inside Bovada's generic "Any other score" bucket:

```powershell
python -m kick_tipp_tipper.cli expected-points 1 --top 10 --infer-other-scores
```

Check which betting markets Bovada exposes for a fixture:

```powershell
python -m kick_tipp_tipper.cli markets 1
```

Useful options:

- `--bovada-path`: Bovada competition path. Defaults to
  `soccer/fifa-world-cup/fifa-world-cup-matches`.
- `--max-goals`: evaluate every prediction from `0-0` to `N-N` instead of only
  score lines quoted by the market. Without this, recommendations are limited
  to quoted score lines even when `--infer-other-scores` is enabled.
- `--infer-other-scores`: split the any-other-score probability across
  estimated unquoted score lines and include those estimates in optimization.
- `--tail-max-goals`: when using `--infer-other-scores`, allocate the
  any-other-score bucket across unquoted score lines up to `N-N`. Defaults to
  `10`.
- `--timezone`: display timezone. Defaults to `Europe/London`.

The odds normalizer includes "Any Other Score" prices in the probability total.
By default, a generic "Any Other Score" bucket is normalized but not assigned to
exact-score, goal-difference, or result points. With `--infer-other-scores`, the
tool keeps the bucket's total market probability and estimates how to distribute
that mass across unquoted score lines.

## Tests

```powershell
python -m unittest discover
```
