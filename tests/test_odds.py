from __future__ import annotations

from http.client import IncompleteRead
import unittest
from unittest.mock import patch

from kick_tipp_tipper.game import MatchResult, ScoreLine
from kick_tipp_tipper.odds import (
    BovadaProvider,
    BookmakerMarket,
    CorrectScoreOutcomeParser,
    OddsError,
    OddsConverter,
    PricedOutcome,
)


def bovada_sample_payload(*, include_any_other: bool = True):
    markets = [
        {
            "description": "Correct Score",
            "period": {"description": "Regulation Time"},
            "outcomes": [
                {
                    "description": "1 - 0",
                    "price": {"decimal": "6.250"},
                },
                {
                    "description": "0 - 0",
                    "price": {"decimal": "8.750"},
                },
                {
                    "description": "0 - 1",
                    "price": {"decimal": "10.500"},
                },
            ],
        }
    ]
    if include_any_other:
        markets.append(
            {
                "description": "Correct Score Group",
                "period": {"description": "Regulation Time"},
                "outcomes": [
                    {
                        "description": "Any other score",
                        "price": {"decimal": "5.00"},
                    }
                ],
            }
        )

    return [
        {
            "path": [
                {"description": "FIFA World Cup Matches"},
                {"description": "Soccer"},
            ],
            "events": [
                {
                    "id": "fixture-1",
                    "description": "Czech Republic vs South Africa",
                    "type": "GAMEEVENT",
                    "startTime": 1781798400000,
                    "competitors": [
                        {"name": "Czechia", "shortName": "Czech Rep.", "home": True},
                        {"name": "South Africa", "home": False},
                    ],
                    "displayGroups": [
                        {
                            "description": "Game Props",
                            "markets": markets,
                        }
                    ],
                }
            ],
        }
    ]


class FakeBovadaProvider(BovadaProvider):
    def __init__(self, *, include_any_other: bool = True) -> None:
        super().__init__()
        self.include_any_other = include_any_other

    def _competition_payload(self):
        return bovada_sample_payload(include_any_other=self.include_any_other)


class FakeHttpResponse:
    def __init__(
        self,
        body: bytes = b"[]",
        *,
        read_error: Exception | None = None,
    ) -> None:
        self.body = body
        self.read_error = read_error

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        return None

    def read(self) -> bytes:
        if self.read_error is not None:
            raise self.read_error
        return self.body


class OddsConverterTest(unittest.TestCase):
    def test_decimal_odds_are_normalized_including_any_other_score(self) -> None:
        market = BookmakerMarket(
            bookmaker_key="book",
            bookmaker_title="Book",
            market_key="Correct Score",
            outcomes=(
                PricedOutcome("1-0", 5.0, scoreline=ScoreLine(1, 0)),
                PricedOutcome("0-0", 10.0, scoreline=ScoreLine(0, 0)),
                PricedOutcome("Any Other Score", 2.0),
            ),
        )

        distribution = OddsConverter().distribution_for_market(market)
        probabilities = {outcome.identity: outcome.probability for outcome in distribution.outcomes}

        self.assertAlmostEqual(sum(probabilities.values()), 1.0)
        self.assertAlmostEqual(probabilities[("score", ScoreLine(1, 0))], 0.25)
        self.assertAlmostEqual(probabilities[("score", ScoreLine(0, 0))], 0.125)
        self.assertAlmostEqual(probabilities[("bucket", "any other score")], 0.625)

    def test_any_other_score_can_be_inferred_across_unquoted_scores(self) -> None:
        market = BookmakerMarket(
            bookmaker_key="book",
            bookmaker_title="Book",
            market_key="Correct Score",
            outcomes=(
                PricedOutcome("1-0", 5.0, scoreline=ScoreLine(1, 0)),
                PricedOutcome("0-0", 10.0, scoreline=ScoreLine(0, 0)),
                PricedOutcome("0-1", 10.0, scoreline=ScoreLine(0, 1)),
                PricedOutcome("Any Other Score", 2.0),
            ),
        )

        distribution = OddsConverter().distribution_for_market(
            market,
            infer_other_scores=True,
            inferred_max_goals=2,
        )

        self.assertAlmostEqual(distribution.total_probability, 1.0)
        self.assertAlmostEqual(distribution.unknown_bucket_probability, 0.0)
        self.assertGreater(distribution.inferred_score_probability, 0.0)
        self.assertGreater(distribution.inferred_score_count, 0)
        self.assertIn(ScoreLine(2, 1), distribution.exact_scorelines)

    def test_american_odds_can_be_converted(self) -> None:
        converter = OddsConverter()

        self.assertAlmostEqual(converter.implied_probability(150, odds_format="american"), 0.4)
        self.assertAlmostEqual(converter.implied_probability(-150, odds_format="american"), 0.6)
        self.assertAlmostEqual(converter.implied_probability(200, odds_format="american"), 1 / 3)

class CorrectScoreOutcomeParserTest(unittest.TestCase):
    def test_parses_exact_score_labels(self) -> None:
        parser = CorrectScoreOutcomeParser()

        outcome = parser.parse(
            label="England 2-1 Brazil",
            price=9.5,
            home_team="England",
            away_team="Brazil",
        )

        self.assertEqual(outcome.scoreline, ScoreLine(2, 1))

    def test_parses_other_home_win_labels_as_partial_buckets(self) -> None:
        parser = CorrectScoreOutcomeParser()

        outcome = parser.parse(
            label="Any Other Home Win",
            price=26.0,
            home_team="England",
            away_team="Brazil",
        )

        self.assertIsNone(outcome.scoreline)
        self.assertEqual(outcome.result, MatchResult.HOME_WIN)

class BovadaProviderTest(unittest.TestCase):
    def test_parses_bovada_fixtures(self) -> None:
        fixtures = FakeBovadaProvider().upcoming_fixtures()

        self.assertEqual(len(fixtures), 1)
        self.assertEqual(fixtures[0].fixture_id, "fixture-1")
        self.assertEqual(fixtures[0].home_team, "Czechia")
        self.assertEqual(fixtures[0].away_team, "South Africa")

    def test_builds_correct_score_market_with_any_other_bucket(self) -> None:
        market = FakeBovadaProvider().correct_score_market("fixture-1")

        self.assertEqual(market.bookmaker_market.bookmaker_key, "bovada")
        self.assertEqual(
            market.quoted_scorelines,
            (ScoreLine(0, 0), ScoreLine(0, 1), ScoreLine(1, 0)),
        )

        distribution = market.to_probability_distribution(infer_other_scores=False)
        identities = {outcome.identity for outcome in distribution.outcomes}

        self.assertIn(("score", ScoreLine(1, 0)), identities)
        self.assertIn(("score", ScoreLine(0, 0)), identities)
        self.assertIn(("score", ScoreLine(0, 1)), identities)
        self.assertIn(("bucket", "any other score"), identities)
        self.assertAlmostEqual(distribution.total_probability, 1.0)

    def test_estimates_missing_any_other_bucket_from_residual_probability(self) -> None:
        market = FakeBovadaProvider(include_any_other=False).correct_score_market("fixture-1")

        self.assertEqual(len(market.warnings), 1)
        self.assertIn("Estimated the missing bucket", market.warnings[0])

        distribution = market.to_probability_distribution(infer_other_scores=False)
        probabilities = {outcome.identity: outcome.probability for outcome in distribution.outcomes}

        expected_residual = 1.0 - (1 / 6.25 + 1 / 8.75 + 1 / 10.5)
        self.assertAlmostEqual(distribution.total_probability, 1.0)
        self.assertAlmostEqual(
            probabilities[("bucket", "estimated any other score")],
            expected_residual,
        )

    def test_correct_score_market_can_infer_any_other_scores(self) -> None:
        market = FakeBovadaProvider().correct_score_market("fixture-1")

        distribution = market.to_probability_distribution(
            infer_other_scores=True,
            inferred_max_goals=2,
        )

        self.assertEqual(distribution.unknown_bucket_probability, 0.0)
        self.assertGreater(distribution.inferred_score_count, 0)

    def test_lists_bovada_available_markets(self) -> None:
        markets = FakeBovadaProvider().available_markets("fixture-1")

        self.assertEqual(markets[0].bookmaker_key, "bovada")
        self.assertEqual(
            markets[0].market_keys,
            ("Correct Score", "Correct Score Group"),
        )

    def test_retries_incomplete_bovada_reads(self) -> None:
        provider = BovadaProvider(retry_attempts=2, retry_backoff_seconds=0)

        with patch(
            "kick_tipp_tipper.odds.urlopen",
            side_effect=[
                FakeHttpResponse(read_error=IncompleteRead(b"partial", 10)),
                FakeHttpResponse(b"[]"),
            ],
        ) as fake_urlopen:
            payload = provider._get_json("https://example.test")

        self.assertEqual(payload, [])
        self.assertEqual(fake_urlopen.call_count, 2)

    def test_incomplete_bovada_reads_raise_clean_odds_error(self) -> None:
        provider = BovadaProvider(retry_attempts=2, retry_backoff_seconds=0)

        with patch(
            "kick_tipp_tipper.odds.urlopen",
            side_effect=[
                FakeHttpResponse(read_error=IncompleteRead(b"partial", 10)),
                FakeHttpResponse(read_error=IncompleteRead(b"partial", 10)),
            ],
        ):
            with self.assertRaises(OddsError) as error:
                provider._get_json("https://example.test")

        self.assertIn("Bovada response ended before the full payload was received", str(error.exception))
        self.assertIn("after 2 attempts", str(error.exception))

    def test_competition_payload_fetches_fresh_data_without_caching(self) -> None:
        provider = BovadaProvider()

        with patch.object(provider, "_get_json", side_effect=[[], []]) as fake_get_json:
            provider._competition_payload()
            provider._competition_payload()

        self.assertEqual(fake_get_json.call_count, 2)


if __name__ == "__main__":
    unittest.main()
