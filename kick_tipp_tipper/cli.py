from __future__ import annotations

import argparse
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone, tzinfo
import os
import sys
from typing import Callable, TextIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .game import Fixture, GameError, PredictionOptimizer
from .odds import (
    BovadaProvider,
    OddsError,
    OddsProvider,
    combine_fixture_query,
)


ProviderFactory = Callable[[argparse.Namespace], OddsProvider]


def main(argv: list[str] | None = None) -> int:
    return execute(argv, provider_factory=_provider_from_args)


def execute(
    argv: list[str] | None,
    *,
    provider_factory: ProviderFactory,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        provider = provider_factory(args)
        args.handler(args, provider, stdout)
    except (GameError, OddsError, ValueError) as exc:
        print(f"error: {exc}", file=stderr)
        return 2

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kick-tipp-tipper",
        description="Optimize Kicktipp World Cup score predictions from correct-score odds.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fixtures = subparsers.add_parser(
        "fixtures", help="Show upcoming fixtures in chronological order."
    )
    _add_provider_arguments(fixtures)
    fixtures.set_defaults(handler=_handle_fixtures)

    markets = subparsers.add_parser(
        "markets",
        help="Show the betting market keys available for one fixture.",
    )
    _add_provider_arguments(markets)
    markets.add_argument(
        "fixture",
        nargs="+",
        help=(
            "Fixture number from the fixtures command, fixture id, or team-name "
            "search such as 'England v Brazil'."
        ),
    )
    markets.set_defaults(handler=_handle_markets)

    expected = subparsers.add_parser(
        "expected-points",
        aliases=["predictions"],
        help="Rank score predictions for one fixture by expected points.",
    )
    _add_provider_arguments(expected)
    expected.add_argument(
        "fixture",
        nargs="+",
        help=(
            "Fixture number from the fixtures command, fixture id, or team-name "
            "search such as 'England v Brazil'."
        ),
    )
    expected.add_argument(
        "--top",
        type=int,
        help="Limit the number of ranked score lines shown.",
    )
    expected.add_argument(
        "--max-goals",
        type=int,
        help="Evaluate every score line from 0-0 up to N-N instead of only quoted scores.",
    )
    expected.set_defaults(handler=_handle_expected_points)

    return parser


def _add_provider_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--timezone",
        default=os.environ.get("KICK_TIPP_TIMEZONE", "Europe/London"),
        help="Timezone for displaying kick-off times.",
    )
    parser.add_argument(
        "--bovada-path",
        default=os.environ.get(
            "KICK_TIPP_BOVADA_PATH",
            "soccer/fifa-world-cup/fifa-world-cup-matches",
        ),
        help="Bovada competition path.",
    )


def _provider_from_args(args: argparse.Namespace) -> OddsProvider:
    return BovadaProvider(competition_path=args.bovada_path)


def _handle_fixtures(
    args: argparse.Namespace, provider: OddsProvider, stdout: TextIO
) -> None:
    fixtures = provider.upcoming_fixtures()
    timezone_info = _timezone_from_args(args.timezone)
    _print_fixtures(fixtures, timezone_info, stdout)


def _handle_markets(
    args: argparse.Namespace, provider: OddsProvider, stdout: TextIO
) -> None:
    fixtures = provider.upcoming_fixtures()
    query = combine_fixture_query(args.fixture)
    fixture_id = _resolve_fixture_id(query, fixtures)
    fixture = next((item for item in fixtures if item.fixture_id == fixture_id), None)
    available_markets = provider.available_markets(fixture_id)

    if fixture is not None:
        print(f"{fixture.label} ({fixture.fixture_id})", file=stdout)
    else:
        print(f"Fixture {fixture_id}", file=stdout)

    if not available_markets:
        print("No markets were returned.", file=stdout)
        return

    for bookmaker in available_markets:
        print(
            f"{bookmaker.bookmaker_key} ({bookmaker.bookmaker_title}): "
            f"{', '.join(bookmaker.market_keys) if bookmaker.market_keys else 'no markets'}",
            file=stdout,
        )


def _handle_expected_points(
    args: argparse.Namespace, provider: OddsProvider, stdout: TextIO
) -> None:
    query = combine_fixture_query(args.fixture)
    fixture_id = _resolve_fixture_id(query, provider.upcoming_fixtures())
    market = provider.correct_score_market(fixture_id)
    distribution = market.to_probability_distribution()
    optimizer = PredictionOptimizer()
    rankings = optimizer.rank_predictions(
        distribution,
        max_goals=args.max_goals,
    )
    if args.top is not None:
        if args.top <= 0:
            raise GameError("--top must be positive.")
        rankings = rankings[: args.top]

    timezone_info = _timezone_from_args(args.timezone)
    _print_expectations(
        fixture=market.fixture,
        rankings=rankings,
        unknown_probability=distribution.unknown_bucket_probability,
        partial_probability=distribution.partial_bucket_probability,
        timezone_info=timezone_info,
        stdout=stdout,
    )


def _resolve_fixture_id(query: str, fixtures: list[Fixture]) -> str:
    ordered_fixtures = sorted(fixtures, key=lambda item: item.commence_time)

    if query.isdecimal():
        fixture_number = int(query)
        if fixture_number <= 0:
            raise GameError("Fixture numbers start at 1.")
        if fixture_number > len(ordered_fixtures):
            raise GameError(
                f"Fixture number {fixture_number} is out of range. "
                f"There are {len(ordered_fixtures)} upcoming fixtures."
            )
        return ordered_fixtures[fixture_number - 1].fixture_id

    exact_id_matches = [fixture for fixture in fixtures if fixture.fixture_id == query]
    if exact_id_matches:
        return exact_id_matches[0].fixture_id

    needle = query.casefold()
    matches = [
        fixture
        for fixture in fixtures
        if needle in fixture.label.casefold()
        or all(part in fixture.label.casefold() for part in needle.split())
    ]
    if not matches:
        return query
    if len(matches) > 1:
        choices = "\n".join(
            f"  {fixture.fixture_id}: {fixture.label}" for fixture in matches
        )
        raise GameError(f"Fixture search matched more than one game:\n{choices}")
    return matches[0].fixture_id


def _print_fixtures(
    fixtures: list[Fixture], timezone_info: tzinfo, stdout: TextIO
) -> None:
    if not fixtures:
        print("No upcoming fixtures were returned.", file=stdout)
        return

    print(
        _table_row(["No.", "Kick-off", "Fixture id", "Fixture"], [5, 18, 24, 1]),
        file=stdout,
    )
    print(
        _table_row(["-" * 3, "-" * 8, "-" * 10, "-" * 7], [5, 18, 24, 1]),
        file=stdout,
    )
    for index, fixture in enumerate(
        sorted(fixtures, key=lambda item: item.commence_time), start=1
    ):
        print(
            _table_row(
                [
                    str(index),
                    _format_time(fixture, timezone_info),
                    fixture.fixture_id,
                    fixture.label,
                ],
                [5, 18, 24, 1],
            ),
            file=stdout,
        )


def _print_expectations(
    *,
    fixture: Fixture,
    rankings,
    unknown_probability: float,
    partial_probability: float,
    timezone_info: tzinfo,
    stdout: TextIO,
) -> None:
    print(
        f"{fixture.label} ({_format_time(fixture, timezone_info)})",
        file=stdout,
    )
    if unknown_probability:
        print(
            f"Unmodelled other-score bucket included in normalisation: {unknown_probability:.1%}",
            file=stdout,
        )
    if partial_probability:
        print(
            f"Partial other-score bucket included in scoring: {partial_probability:.1%}",
            file=stdout,
        )

    print("", file=stdout)
    print(
        _table_row(
            ["Score", "Expected", "Exact p", "GD p", "Result p"],
            [7, 10, 9, 9, 9],
        ),
        file=stdout,
    )
    print(
        _table_row(["-" * 5, "-" * 8, "-" * 7, "-" * 4, "-" * 8], [7, 10, 9, 9, 9]),
        file=stdout,
    )
    for ranking in rankings:
        print(
            _table_row(
                [
                    str(ranking.scoreline),
                    f"{ranking.expected_points:.3f}",
                    f"{ranking.exact_score_probability:.1%}",
                    f"{ranking.goal_difference_probability:.1%}",
                    f"{ranking.result_probability:.1%}",
                ],
                [7, 10, 9, 9, 9],
            ),
            file=stdout,
        )


def _table_row(values: list[str], widths: list[int]) -> str:
    cells = []
    for value, width in zip(values, widths, strict=True):
        if width == 1:
            cells.append(value)
        else:
            cells.append(value.ljust(width))
    return "  ".join(cells).rstrip()


def _timezone_from_args(value: str) -> tzinfo:
    if value.casefold() in {"utc", "z"}:
        return timezone.utc
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        if value == "Europe/London":
            return LondonTimeZone()
        raise ValueError(f"Unknown timezone: {value!r}.") from exc


def _format_time(fixture: Fixture, timezone_info: tzinfo) -> str:
    commence_time = fixture.commence_time
    if commence_time.tzinfo is None:
        commence_time = commence_time.replace(tzinfo=timezone.utc)
    return commence_time.astimezone(timezone_info).strftime("%Y-%m-%d %H:%M")


class LondonTimeZone(tzinfo):
    """Fallback for Windows installs without IANA timezone data."""

    def fromutc(self, dt: datetime) -> datetime:
        if dt.tzinfo is not self:
            raise ValueError("fromutc: dt.tzinfo is not self")
        utc_dt = dt.replace(tzinfo=timezone.utc)
        offset = timedelta(hours=1) if _is_bst_utc(utc_dt) else timedelta(0)
        return (dt + offset).replace(tzinfo=self)

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return self.dst(dt)

    def dst(self, dt: datetime | None) -> timedelta:
        if dt is None:
            return timedelta(0)
        local_dt = dt.replace(tzinfo=None)
        start = datetime(dt.year, 3, _last_sunday(dt.year, 3).day, 2)
        end = datetime(dt.year, 10, _last_sunday(dt.year, 10).day, 2)
        return timedelta(hours=1) if start <= local_dt < end else timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        return "BST" if self.dst(dt) else "GMT"


def _is_bst_utc(dt: datetime) -> bool:
    start = datetime(dt.year, 3, _last_sunday(dt.year, 3).day, 1, tzinfo=timezone.utc)
    end = datetime(dt.year, 10, _last_sunday(dt.year, 10).day, 1, tzinfo=timezone.utc)
    return start <= dt < end


def _last_sunday(year: int, month: int) -> date:
    day = date(year, month, monthrange(year, month)[1])
    while day.weekday() != 6:
        day -= timedelta(days=1)
    return day


if __name__ == "__main__":
    raise SystemExit(main())
