from __future__ import annotations

import unittest

from kick_tipp_tipper.game import MatchResult, ScoreLine
from kick_tipp_tipper.odds import (
    BovadaProvider,
    BookmakerMarket,
    CorrectScoreOutcomeParser,
    OddsConverter,
    PricedOutcome,
)


def bovada_sample_payload():
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
                            "markets": [
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
                                },
                                {
                                    "description": "Correct Score Group",
                                    "period": {"description": "Regulation Time"},
                                    "outcomes": [
                                        {
                                            "description": "Any other score",
                                            "price": {"decimal": "5.00"},
                                        }
                                    ],
                                },
                            ],
                        }
                    ],
                }
            ],
        }
    ]


class FakeBovadaProvider(BovadaProvider):
    def _competition_payload(self):
        return bovada_sample_payload()


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

        distribution = market.to_probability_distribution()
        identities = {outcome.identity for outcome in distribution.outcomes}

        self.assertIn(("score", ScoreLine(1, 0)), identities)
        self.assertIn(("score", ScoreLine(0, 0)), identities)
        self.assertIn(("score", ScoreLine(0, 1)), identities)
        self.assertIn(("bucket", "any other score"), identities)
        self.assertAlmostEqual(distribution.total_probability, 1.0)

    def test_lists_bovada_available_markets(self) -> None:
        markets = FakeBovadaProvider().available_markets("fixture-1")

        self.assertEqual(markets[0].bookmaker_key, "bovada")
        self.assertEqual(
            markets[0].market_keys,
            ("Correct Score", "Correct Score Group"),
        )


if __name__ == "__main__":
    unittest.main()
