from __future__ import annotations

from datetime import datetime, timezone
import io
import unittest

from kick_tipp_tipper.cli import execute
from kick_tipp_tipper.game import Fixture, ScoreLine
from kick_tipp_tipper.odds import (
    BookmakerAvailableMarkets,
    BookmakerMarket,
    CorrectScoreMarket,
    PricedOutcome,
)


class FakeProvider:
    def __init__(self) -> None:
        self.requested_fixture_ids: list[str] = []
        self.fixtures = [
            Fixture(
                fixture_id="late",
                home_team="Brazil",
                away_team="Germany",
                commence_time=datetime(2026, 6, 20, 20, 0, tzinfo=timezone.utc),
            ),
            Fixture(
                fixture_id="early",
                home_team="England",
                away_team="France",
                commence_time=datetime(2026, 6, 18, 18, 0, tzinfo=timezone.utc),
            ),
        ]

    def upcoming_fixtures(self) -> list[Fixture]:
        return self.fixtures

    def correct_score_market(self, fixture_id: str) -> CorrectScoreMarket:
        self.requested_fixture_ids.append(fixture_id)
        fixture = next(item for item in self.fixtures if item.fixture_id == fixture_id)
        return CorrectScoreMarket(
            fixture=fixture,
            bookmaker_market=BookmakerMarket(
                bookmaker_key="test",
                bookmaker_title="Test Book",
                market_key="Correct Score",
                outcomes=(
                    PricedOutcome("1-0", 3.0, scoreline=ScoreLine(1, 0)),
                    PricedOutcome("0-0", 6.0, scoreline=ScoreLine(0, 0)),
                    PricedOutcome("Any Other Score", 6.0),
                ),
            ),
        )

    def available_markets(self, fixture_id: str) -> list[BookmakerAvailableMarkets]:
        self.requested_fixture_ids.append(fixture_id)
        return [
            BookmakerAvailableMarkets(
                bookmaker_key="test",
                bookmaker_title="Test Book",
                market_keys=("h2h", "totals"),
            )
        ]


def fake_provider_factory(_args):
    return FakeProvider()


class CliTest(unittest.TestCase):
    def test_fixtures_command_prints_chronological_fixtures(self) -> None:
        stdout = io.StringIO()

        exit_code = execute(
            ["fixtures", "--timezone", "UTC"],
            provider_factory=fake_provider_factory,
            stdout=stdout,
        )

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertLess(output.index("England v France"), output.index("Brazil v Germany"))
        self.assertIn("No.", output)
        self.assertIn("1", output)

    def test_expected_points_command_prints_ranked_scores(self) -> None:
        stdout = io.StringIO()

        exit_code = execute(
            ["expected-points", "early", "--timezone", "UTC"],
            provider_factory=fake_provider_factory,
            stdout=stdout,
        )

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("England v France", output)
        self.assertIn("1-0", output)
        self.assertLess(output.index("1-0"), output.index("0-0"))

    def test_expected_points_command_accepts_chronological_fixture_number(self) -> None:
        stdout = io.StringIO()
        provider = FakeProvider()

        exit_code = execute(
            ["expected-points", "1", "--timezone", "UTC"],
            provider_factory=lambda _args: provider,
            stdout=stdout,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(provider.requested_fixture_ids, ["early"])
        self.assertIn("England v France", stdout.getvalue())

    def test_markets_command_accepts_chronological_fixture_number(self) -> None:
        stdout = io.StringIO()
        provider = FakeProvider()

        exit_code = execute(
            ["markets", "1", "--timezone", "UTC"],
            provider_factory=lambda _args: provider,
            stdout=stdout,
        )

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertEqual(provider.requested_fixture_ids, ["early"])
        self.assertIn("England v France", output)
        self.assertIn("h2h, totals", output)

    def test_expected_points_command_rejects_out_of_range_fixture_number(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        exit_code = execute(
            ["expected-points", "3", "--timezone", "UTC"],
            provider_factory=fake_provider_factory,
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(exit_code, 2)
        self.assertIn("out of range", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
