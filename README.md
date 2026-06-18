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

## How it works

### Turning odds into probabilities

The tool reads Bovada's correct-score market for a fixture. Each price is turned
into a raw implied probability:

```text
implied probability = 1 / decimal odds
```

Those raw probabilities include the quoted exact scores and the generic
`Any other score` outcome. They are then normalised so the whole market sums to
one:

```text
normalised probability = raw probability / sum(all raw market probabilities)
```

Including `Any other score` in that total matters because otherwise the quoted
low-score outcomes would be treated as if they covered every possible match
score. They do not.

### Calculating expected points

For each possible prediction, the optimizer checks every possible actual outcome
in the probability distribution and assigns it to exactly one scoring category:

- exact score: 4 points
- same non-draw goal difference: 3 points
- same result: 2 points
- anything else: 0 points

The expected points for a prediction are the probability-weighted average of
those scores:

```text
expected points =
    4 * P(exact score)
  + 3 * P(correct non-draw goal difference, excluding exact score)
  + 2 * P(correct result, excluding exact score and goal difference)
```

The categories are mutually exclusive. For example, if you predict `2-1` and the
match finishes `2-1`, that contributes to the 4-point exact-score probability,
not also to the 3-point goal-difference or 2-point result probabilities.

For non-draw goal difference, a `2-1` prediction gets 3 points from outcomes
such as `1-0`, `3-2`, or `4-3`, but not from `2-1` itself because that is already
the exact-score case. Draw goal differences are deliberately excluded from the
3-point rule, matching the game rules.

By default, `expected-points` only ranks score lines that are explicitly quoted
in the correct-score market. Use `--max-goals N` if you want to rank every score
line from `0-0` to `N-N`.

### Inferring unquoted scores from Any Other Score

Bovada's market usually quotes common scores individually and groups everything
else into `Any other score`. Without `--infer-other-scores`, that bucket is still
included in the normalisation, but it is left as an unknown bucket. Because it
has no exact score attached to it, it cannot contribute exact-score,
goal-difference, or result points.

With `--infer-other-scores`, the tool keeps the market's actual
`Any other score` probability and estimates how to split that probability across
individual unquoted score lines.

The split uses a simple independent Poisson model:

```text
P(home goals = h) = Poisson(h; lambda_home)
P(away goals = a) = Poisson(a; lambda_away)

P(score h-a) = P(home goals = h) * P(away goals = a)
```

`lambda_home` and `lambda_away` are fitted from the shape of the quoted
correct-score market:

1. The quoted exact-score probabilities are re-normalised so they sum to one
   within the quoted score set.
2. The tool tries many possible `lambda_home` and `lambda_away` pairs.
3. For each pair, it calculates Poisson probabilities for the same quoted score
   lines and re-normalises those probabilities over the quoted score set.
4. It chooses the lambda pair whose quoted-score shape is closest to the
   market's quoted-score shape.

After fitting those lambdas, the tool calculates Poisson weights for every
unquoted score line up to `--tail-max-goals N`. It then scales those weights so
their total is exactly the market's `Any other score` probability:

```text
inferred P(unquoted score) =
    Any-other-score market probability
  * Poisson weight for that score
  / sum(Poisson weights for all inferred unquoted scores)
```

This means the tool is not inventing the size of the tail. The bookmaker market
provides the total `Any other score` probability; the Poisson model only decides
how to divide that total across particular unquoted scores.

The inference is optional because it is still a model assumption. It is useful
when you want the optimizer to account for high-scoring or unusual results that
are hidden inside `Any other score`, but the default behaviour stays closer to
the directly quoted market.

## Tests

```powershell
python -m unittest discover
```
