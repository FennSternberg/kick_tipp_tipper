from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import re
from typing import Iterable, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .game import (
    ActualOutcome,
    Fixture,
    GameError,
    MatchResult,
    ProbabilityDistribution,
    ScoreLine,
)


class OddsError(RuntimeError):
    """Raised when odds cannot be retrieved or interpreted."""


class OddsProvider(Protocol):
    def upcoming_fixtures(self) -> list[Fixture]:
        ...

    def correct_score_market(self, fixture_id: str) -> "CorrectScoreMarket":
        ...

    def available_markets(self, fixture_id: str) -> list["BookmakerAvailableMarkets"]:
        ...


@dataclass(frozen=True)
class PricedOutcome:
    label: str
    price: float
    scoreline: ScoreLine | None = None
    result: MatchResult | None = None
    goal_difference: int | None = None

    @property
    def identity(self) -> tuple[str, object]:
        if self.scoreline is not None:
            return ("score", self.scoreline)
        if self.goal_difference is not None:
            return ("goal_difference", self.goal_difference)
        if self.result is not None:
            return ("result", self.result)
        return ("bucket", self.label.strip().casefold())

    def to_actual_outcome(self, probability: float) -> ActualOutcome:
        return ActualOutcome(
            label=self.label,
            probability=probability,
            scoreline=self.scoreline,
            result=self.result,
            goal_difference=self.goal_difference,
        )


@dataclass(frozen=True)
class BookmakerMarket:
    bookmaker_key: str
    bookmaker_title: str
    market_key: str
    outcomes: tuple[PricedOutcome, ...]


@dataclass(frozen=True)
class BookmakerAvailableMarkets:
    bookmaker_key: str
    bookmaker_title: str
    market_keys: tuple[str, ...]


@dataclass(frozen=True)
class CorrectScoreMarket:
    fixture: Fixture
    bookmaker_market: BookmakerMarket

    @property
    def quoted_scorelines(self) -> tuple[ScoreLine, ...]:
        scorelines = {
            outcome.scoreline
            for outcome in self.bookmaker_market.outcomes
            if outcome.scoreline is not None
        }
        return tuple(
            sorted(
                scorelines,
                key=lambda scoreline: (
                    scoreline.home_goals + scoreline.away_goals,
                    scoreline.home_goals,
                    scoreline.away_goals,
                ),
            )
        )

    def to_probability_distribution(
        self,
        *,
        infer_other_scores: bool = False,
        inferred_max_goals: int = 10,
    ) -> ProbabilityDistribution:
        return OddsConverter().distribution_for_market(
            self.bookmaker_market,
            infer_other_scores=infer_other_scores,
            inferred_max_goals=inferred_max_goals,
        )


class OddsConverter:
    def implied_probability(self, price: float, *, odds_format: str = "decimal") -> float:
        if odds_format == "decimal":
            if price <= 1:
                raise OddsError("Decimal odds must be greater than 1.0.")
            return 1.0 / price

        if odds_format == "american":
            if price == 0:
                raise OddsError("American odds cannot be zero.")
            if price > 0:
                return 100.0 / (price + 100.0)
            return abs(price) / (abs(price) + 100.0)

        raise OddsError(f"Unsupported odds format: {odds_format!r}.")

    def distribution_for_market(
        self,
        market: BookmakerMarket,
        *,
        odds_format: str = "decimal",
        infer_other_scores: bool = False,
        inferred_max_goals: int = 10,
    ) -> ProbabilityDistribution:
        if not market.outcomes:
            raise OddsError("The selected market has no outcomes.")
        if inferred_max_goals < 0:
            raise OddsError("inferred_max_goals cannot be negative.")

        raw_outcomes: dict[tuple[str, object], ActualOutcome] = {}
        raw_probabilities: dict[tuple[str, object], float] = {}

        for outcome in market.outcomes:
            raw_probability = self.implied_probability(
                outcome.price, odds_format=odds_format
            )
            key = outcome.identity
            raw_probabilities[key] = raw_probabilities.get(key, 0.0) + raw_probability
            raw_outcomes.setdefault(key, outcome.to_actual_outcome(0.0))

        total = sum(raw_probabilities.values())
        if total <= 0:
            raise OddsError("The selected market has no positive probability mass.")

        distribution = ProbabilityDistribution(
            tuple(
                raw_outcomes[key].with_probability(raw_probability / total)
                for key, raw_probability in raw_probabilities.items()
            )
        )
        if not infer_other_scores:
            return distribution
        return AnyOtherScoreInferer(max_goals=inferred_max_goals).expand(distribution)


class AnyOtherScoreInferer:
    def __init__(
        self,
        *,
        max_goals: int = 10,
        min_expected_goals: float = 0.1,
        max_expected_goals: float = 5.5,
        grid_step: float = 0.05,
    ) -> None:
        if max_goals < 0:
            raise OddsError("max_goals cannot be negative.")
        self.max_goals = max_goals
        self.min_expected_goals = min_expected_goals
        self.max_expected_goals = max_expected_goals
        self.grid_step = grid_step

    def expand(self, distribution: ProbabilityDistribution) -> ProbabilityDistribution:
        exact_outcomes = [
            outcome for outcome in distribution.outcomes if outcome.scoreline is not None
        ]
        other_outcomes = [
            outcome
            for outcome in distribution.outcomes
            if outcome.scoreline is None and _is_generic_any_other_score(outcome.label)
        ]
        retained_bucket_outcomes = [
            outcome
            for outcome in distribution.outcomes
            if outcome.scoreline is None and not _is_generic_any_other_score(outcome.label)
        ]
        other_probability = sum(outcome.probability for outcome in other_outcomes)

        if not exact_outcomes or other_probability <= 0:
            return distribution

        quoted_scorelines = {outcome.scoreline for outcome in exact_outcomes}
        highest_quoted_goal = max(
            max(scoreline.home_goals, scoreline.away_goals)
            for scoreline in quoted_scorelines
            if scoreline is not None
        )
        max_goals = max(self.max_goals, highest_quoted_goal)
        candidate_scorelines = [
            ScoreLine(home_goals, away_goals)
            for home_goals in range(max_goals + 1)
            for away_goals in range(max_goals + 1)
            if ScoreLine(home_goals, away_goals) not in quoted_scorelines
        ]
        if not candidate_scorelines:
            return distribution

        home_expected_goals, away_expected_goals = self._fit_expected_goals(
            exact_outcomes
        )
        candidate_weights = {
            scoreline: _poisson_probability(
                scoreline.home_goals,
                home_expected_goals,
            )
            * _poisson_probability(scoreline.away_goals, away_expected_goals)
            for scoreline in candidate_scorelines
        }
        candidate_total = sum(candidate_weights.values())
        if candidate_total <= 0:
            return distribution

        inferred_outcomes = [
            ActualOutcome.exact(
                scoreline,
                other_probability * weight / candidate_total,
            )
            for scoreline, weight in candidate_weights.items()
        ]

        return ProbabilityDistribution(
            tuple(exact_outcomes + retained_bucket_outcomes + inferred_outcomes),
            inferred_score_probability=other_probability,
            inferred_score_count=len(inferred_outcomes),
        )

    def _fit_expected_goals(
        self, exact_outcomes: Sequence[ActualOutcome]
    ) -> tuple[float, float]:
        exact_probability = sum(outcome.probability for outcome in exact_outcomes)
        if exact_probability <= 0:
            raise OddsError("Cannot infer other scores without exact-score probability.")

        observed_conditional = {
            outcome.scoreline: outcome.probability / exact_probability
            for outcome in exact_outcomes
            if outcome.scoreline is not None
        }
        candidates = [
            self.min_expected_goals + index * self.grid_step
            for index in range(
                int(
                    round(
                        (self.max_expected_goals - self.min_expected_goals)
                        / self.grid_step
                    )
                )
                + 1
            )
        ]

        best_loss: float | None = None
        best_expected_goals = (1.0, 1.0)
        for home_expected_goals in candidates:
            home_probabilities = {
                scoreline.home_goals: _poisson_probability(
                    scoreline.home_goals,
                    home_expected_goals,
                )
                for scoreline in observed_conditional
            }
            for away_expected_goals in candidates:
                model_weights = {
                    scoreline: home_probabilities[scoreline.home_goals]
                    * _poisson_probability(
                        scoreline.away_goals,
                        away_expected_goals,
                    )
                    for scoreline in observed_conditional
                }
                model_total = sum(model_weights.values())
                if model_total <= 0:
                    continue
                loss = -sum(
                    observed_probability
                    * math.log(max(model_weights[scoreline] / model_total, 1e-15))
                    for scoreline, observed_probability in observed_conditional.items()
                )
                if best_loss is None or loss < best_loss:
                    best_loss = loss
                    best_expected_goals = (home_expected_goals, away_expected_goals)

        return best_expected_goals


def _poisson_probability(goals: int, expected_goals: float) -> float:
    return (
        math.exp(-expected_goals)
        * expected_goals**goals
        / math.factorial(goals)
    )


def _is_generic_any_other_score(label: str) -> bool:
    text = label.casefold()
    return "any other" in text and "score" in text

class CorrectScoreOutcomeParser:
    _EXACT_SCORE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[-:]\s*(\d{1,2})(?!\d)")

    def parse(
        self,
        *,
        label: str,
        price: float,
        home_team: str,
        away_team: str,
    ) -> PricedOutcome:
        scoreline = self._parse_scoreline(label)
        if scoreline is not None:
            return PricedOutcome(label=label, price=price, scoreline=scoreline)

        result = self._parse_other_result(label, home_team, away_team)
        return PricedOutcome(label=label, price=price, result=result)

    def _parse_scoreline(self, label: str) -> ScoreLine | None:
        match = self._EXACT_SCORE_RE.search(label)
        if not match:
            return None
        return ScoreLine(int(match.group(1)), int(match.group(2)))

    def _parse_other_result(
        self, label: str, home_team: str, away_team: str
    ) -> MatchResult | None:
        text = label.casefold()
        home = home_team.casefold()
        away = away_team.casefold()

        if "draw" in text or "tie" in text:
            return MatchResult.DRAW
        if "home" in text and "win" in text:
            return MatchResult.HOME_WIN
        if "away" in text and "win" in text:
            return MatchResult.AWAY_WIN
        if home in text and "win" in text:
            return MatchResult.HOME_WIN
        if away in text and "win" in text:
            return MatchResult.AWAY_WIN
        return None


class BovadaProvider:
    BASE_URL = "https://www.bovada.lv/services/sports/event/v2/events/A/description"

    def __init__(
        self,
        *,
        competition_path: str = "soccer/fifa-world-cup/fifa-world-cup-matches",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.competition_path = competition_path.strip("/")
        self.timeout_seconds = timeout_seconds
        self.parser = CorrectScoreOutcomeParser()

    def upcoming_fixtures(self) -> list[Fixture]:
        payload = self._competition_payload()
        fixtures = [
            self._parse_fixture(event)
            for event in self._iter_events(payload)
            if self._is_match_event(event)
        ]
        return sorted(_deduplicate_fixtures(fixtures), key=lambda item: item.commence_time)

    def correct_score_market(self, fixture_id: str) -> CorrectScoreMarket:
        event = self._find_event(fixture_id)
        fixture = self._parse_fixture(event)
        exact_market = self._find_market(event, "Correct Score", period="Regulation Time")
        if exact_market is None:
            raise OddsError(f"Bovada did not return a Correct Score market for {fixture.label}.")

        outcomes = self._parse_correct_score_outcomes(exact_market, fixture)
        any_other = self._find_any_other_correct_score_outcome(event)
        if any_other is None:
            raise OddsError(
                "Bovada returned exact correct-score odds but no any-other-score "
                f"bucket for {fixture.label}; refusing to normalize incomplete odds."
            )
        outcomes.append(any_other)

        return CorrectScoreMarket(
            fixture=fixture,
            bookmaker_market=BookmakerMarket(
                bookmaker_key="bovada",
                bookmaker_title="Bovada",
                market_key="Correct Score",
                outcomes=tuple(outcomes),
            ),
        )

    def available_markets(self, fixture_id: str) -> list[BookmakerAvailableMarkets]:
        event = self._find_event(fixture_id)
        market_keys = sorted(
            {
                str(market.get("description", ""))
                for display_group in event.get("displayGroups", [])
                for market in display_group.get("markets", [])
                if market.get("description")
            }
        )
        return [
            BookmakerAvailableMarkets(
                bookmaker_key="bovada",
                bookmaker_title="Bovada",
                market_keys=tuple(market_keys),
            )
        ]

    def _competition_payload(self) -> object:
        return self._get_json(f"{self.BASE_URL}/{self.competition_path}")

    def _get_json(self, url: str) -> object:
        request = Request(
            url,
            headers={
                "User-Agent": "kick-tipp-tipper/0.1",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise OddsError(f"Bovada returned HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise OddsError(f"Could not reach Bovada: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise OddsError("Bovada response was not valid JSON.") from exc

    def _find_event(self, fixture_id: str) -> dict[str, object]:
        payload = self._competition_payload()
        for event in self._iter_events(payload):
            if str(event.get("id")) == fixture_id:
                return event
        raise OddsError(f"Bovada fixture {fixture_id!r} was not found.")

    def _iter_events(self, payload: object) -> Iterable[dict[str, object]]:
        if not isinstance(payload, list):
            raise OddsError("Bovada response was not a list.")
        for group in payload:
            if not isinstance(group, dict):
                continue
            for event in group.get("events", []):
                if isinstance(event, dict):
                    yield event

    def _is_match_event(self, event: dict[str, object]) -> bool:
        competitors = event.get("competitors")
        return (
            event.get("type") == "GAMEEVENT"
            and isinstance(competitors, list)
            and len(competitors) >= 2
            and event.get("startTime") is not None
        )

    def _parse_fixture(self, event: dict[str, object]) -> Fixture:
        competitors = event.get("competitors", [])
        if not isinstance(competitors, list):
            raise OddsError("Bovada event is missing competitors.")

        home_team = None
        away_team = None
        for competitor in competitors:
            if not isinstance(competitor, dict):
                continue
            name = str(competitor.get("name") or competitor.get("shortName") or "")
            if competitor.get("home") is True:
                home_team = name
            elif competitor.get("home") is False:
                away_team = name

        if not home_team or not away_team:
            description = str(event.get("description", ""))
            if " vs " not in description:
                raise OddsError("Bovada event is missing home/away teams.")
            home_team, away_team = description.split(" vs ", 1)

        try:
            start_time_ms = int(str(event["startTime"]))
        except (KeyError, ValueError) as exc:
            raise OddsError("Bovada event is missing a valid start time.") from exc

        return Fixture(
            fixture_id=str(event["id"]),
            home_team=home_team,
            away_team=away_team,
            commence_time=datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc),
        )

    def _find_market(
        self,
        event: dict[str, object],
        description: str,
        *,
        period: str | None = None,
    ) -> dict[str, object] | None:
        for display_group in event.get("displayGroups", []):
            for market in display_group.get("markets", []):
                if market.get("description") != description:
                    continue
                if period is not None:
                    market_period = market.get("period", {})
                    if (
                        not isinstance(market_period, dict)
                        or market_period.get("description") != period
                    ):
                        continue
                return market
        return None

    def _parse_correct_score_outcomes(
        self, market: dict[str, object], fixture: Fixture
    ) -> list[PricedOutcome]:
        outcomes: list[PricedOutcome] = []
        for outcome in market.get("outcomes", []):
            if not isinstance(outcome, dict):
                continue
            price = _bovada_decimal_price(outcome)
            if price is None:
                continue
            outcomes.append(
                self.parser.parse(
                    label=str(outcome.get("description", "")),
                    price=price,
                    home_team=fixture.home_team,
                    away_team=fixture.away_team,
                )
            )

        exact_outcomes = [outcome for outcome in outcomes if outcome.scoreline is not None]
        if not exact_outcomes:
            raise OddsError("Bovada Correct Score market did not contain exact scores.")
        return exact_outcomes

    def _find_any_other_correct_score_outcome(
        self, event: dict[str, object]
    ) -> PricedOutcome | None:
        candidate_market_names = {
            "Correct Score Group",
            "Correct Score Group (2)",
        }
        for display_group in event.get("displayGroups", []):
            for market in display_group.get("markets", []):
                if market.get("description") not in candidate_market_names:
                    continue
                market_period = market.get("period", {})
                if (
                    not isinstance(market_period, dict)
                    or market_period.get("description") != "Regulation Time"
                ):
                    continue
                for outcome in market.get("outcomes", []):
                    if not isinstance(outcome, dict):
                        continue
                    label = str(outcome.get("description", ""))
                    if "any other" not in label.casefold():
                        continue
                    price = _bovada_decimal_price(outcome)
                    if price is None:
                        continue
                    return PricedOutcome(label=label, price=price)
        return None


def _bovada_decimal_price(outcome: dict[str, object]) -> float | None:
    price = outcome.get("price", {})
    if not isinstance(price, dict):
        return None
    decimal = price.get("decimal")
    if decimal is None:
        return None
    return float(str(decimal))


def _deduplicate_fixtures(fixtures: Iterable[Fixture]) -> list[Fixture]:
    by_id: dict[str, Fixture] = {}
    by_label_time: dict[tuple[str, datetime], Fixture] = {}
    for fixture in fixtures:
        key = (fixture.label.casefold(), fixture.commence_time)
        if fixture.fixture_id in by_id or key in by_label_time:
            continue
        by_id[fixture.fixture_id] = fixture
        by_label_time[key] = fixture
    return list(by_id.values())


def combine_fixture_query(query_parts: Sequence[str]) -> str:
    query = " ".join(query_parts).strip()
    if not query:
        raise GameError("A fixture id or team search is required.")
    return query
