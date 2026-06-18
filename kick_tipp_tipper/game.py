from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import re
from typing import Iterable, Sequence


class GameError(ValueError):
    """Raised when game input is invalid."""


class MatchResult(Enum):
    HOME_WIN = "home"
    DRAW = "draw"
    AWAY_WIN = "away"

    @classmethod
    def from_goal_difference(cls, goal_difference: int) -> "MatchResult":
        if goal_difference > 0:
            return cls.HOME_WIN
        if goal_difference < 0:
            return cls.AWAY_WIN
        return cls.DRAW


class ScoringCategory(Enum):
    EXACT_SCORE = "exact_score"
    GOAL_DIFFERENCE = "goal_difference"
    RESULT = "result"
    NONE = "none"


_SCORE_LINE_RE = re.compile(r"^\s*(\d+)\s*[-:]\s*(\d+)\s*$")


@dataclass(frozen=True, order=True)
class ScoreLine:
    home_goals: int
    away_goals: int

    def __post_init__(self) -> None:
        if self.home_goals < 0 or self.away_goals < 0:
            raise GameError("Score lines cannot contain negative goals.")

    @classmethod
    def parse(cls, text: str) -> "ScoreLine":
        match = _SCORE_LINE_RE.match(text)
        if not match:
            raise GameError(f"Expected a score line like '2-1', got {text!r}.")
        return cls(int(match.group(1)), int(match.group(2)))

    @property
    def goal_difference(self) -> int:
        return self.home_goals - self.away_goals

    @property
    def result(self) -> MatchResult:
        return MatchResult.from_goal_difference(self.goal_difference)

    def __str__(self) -> str:
        return f"{self.home_goals}-{self.away_goals}"


@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    home_team: str
    away_team: str
    commence_time: datetime

    @property
    def label(self) -> str:
        return f"{self.home_team} v {self.away_team}"


@dataclass(frozen=True)
class ActualOutcome:
    """A possible true match outcome with probability.

    Exact score lines carry full scoring information. Buckets such as
    "Any Other Home Win" can carry partial information, so they may contribute
    result points but not exact-score or goal-difference points.
    """

    label: str
    probability: float
    scoreline: ScoreLine | None = None
    result: MatchResult | None = None
    goal_difference: int | None = None

    def __post_init__(self) -> None:
        if self.probability < 0:
            raise GameError("Outcome probabilities cannot be negative.")

        if self.scoreline is not None:
            expected_result = self.scoreline.result
            expected_difference = self.scoreline.goal_difference
            if self.result is not None and self.result != expected_result:
                raise GameError("Exact score outcome has an inconsistent result.")
            if (
                self.goal_difference is not None
                and self.goal_difference != expected_difference
            ):
                raise GameError(
                    "Exact score outcome has an inconsistent goal difference."
                )
            object.__setattr__(self, "result", expected_result)
            object.__setattr__(self, "goal_difference", expected_difference)

    @classmethod
    def exact(cls, scoreline: ScoreLine, probability: float) -> "ActualOutcome":
        return cls(label=str(scoreline), probability=probability, scoreline=scoreline)

    @classmethod
    def bucket(
        cls,
        label: str,
        probability: float,
        *,
        result: MatchResult | None = None,
        goal_difference: int | None = None,
    ) -> "ActualOutcome":
        return cls(
            label=label,
            probability=probability,
            result=result,
            goal_difference=goal_difference,
        )

    @property
    def identity(self) -> tuple[str, object]:
        if self.scoreline is not None:
            return ("score", self.scoreline)
        if self.goal_difference is not None:
            return ("goal_difference", self.goal_difference)
        if self.result is not None:
            return ("result", self.result)
        return ("bucket", self.label.strip().casefold())

    def with_probability(self, probability: float) -> "ActualOutcome":
        return ActualOutcome(
            label=self.label,
            probability=probability,
            scoreline=self.scoreline,
            result=self.result,
            goal_difference=self.goal_difference,
        )


@dataclass(frozen=True)
class ProbabilityDistribution:
    outcomes: tuple[ActualOutcome, ...]
    inferred_score_probability: float = 0.0
    inferred_score_count: int = 0

    def __post_init__(self) -> None:
        if not self.outcomes:
            raise GameError("A probability distribution needs at least one outcome.")
        if self.inferred_score_probability < 0:
            raise GameError("Inferred probability cannot be negative.")
        if self.inferred_score_count < 0:
            raise GameError("Inferred score count cannot be negative.")
        total = self.total_probability
        if total <= 0:
            raise GameError("A probability distribution must have positive mass.")
        if abs(total - 1.0) > 1e-6:
            raise GameError(
                f"Outcome probabilities must sum to 1.0, but summed to {total:.6f}."
            )

    @classmethod
    def normalized(cls, outcomes: Iterable[ActualOutcome]) -> "ProbabilityDistribution":
        collected = tuple(outcomes)
        if not collected:
            raise GameError("Cannot normalize an empty set of outcomes.")
        total = sum(outcome.probability for outcome in collected)
        if total <= 0:
            raise GameError("Cannot normalize outcomes with no probability mass.")
        return cls(
            tuple(
                outcome.with_probability(outcome.probability / total)
                for outcome in collected
            )
        )

    @property
    def total_probability(self) -> float:
        return sum(outcome.probability for outcome in self.outcomes)

    @property
    def exact_scorelines(self) -> tuple[ScoreLine, ...]:
        scorelines = {outcome.scoreline for outcome in self.outcomes if outcome.scoreline}
        return tuple(sorted(scorelines, key=lambda score: (score.home_goals + score.away_goals, score.home_goals, score.away_goals)))

    @property
    def unknown_bucket_probability(self) -> float:
        return sum(
            outcome.probability
            for outcome in self.outcomes
            if outcome.scoreline is None
            and outcome.result is None
            and outcome.goal_difference is None
        )

    @property
    def partial_bucket_probability(self) -> float:
        return sum(
            outcome.probability
            for outcome in self.outcomes
            if outcome.scoreline is None
            and (outcome.result is not None or outcome.goal_difference is not None)
        )


@dataclass(frozen=True)
class ScoringRules:
    exact_score_points: int = 4
    goal_difference_points: int = 3
    result_points: int = 2

    def category_for(
        self, prediction: ScoreLine, actual: ActualOutcome
    ) -> ScoringCategory:
        if actual.scoreline == prediction:
            return ScoringCategory.EXACT_SCORE

        if (
            prediction.goal_difference != 0
            and actual.goal_difference == prediction.goal_difference
        ):
            return ScoringCategory.GOAL_DIFFERENCE

        if actual.result == prediction.result:
            return ScoringCategory.RESULT

        return ScoringCategory.NONE

    def points_for_category(self, category: ScoringCategory) -> int:
        if category == ScoringCategory.EXACT_SCORE:
            return self.exact_score_points
        if category == ScoringCategory.GOAL_DIFFERENCE:
            return self.goal_difference_points
        if category == ScoringCategory.RESULT:
            return self.result_points
        return 0

    def points_for(self, prediction: ScoreLine, actual: ActualOutcome) -> int:
        return self.points_for_category(self.category_for(prediction, actual))


@dataclass(frozen=True)
class PredictionExpectation:
    scoreline: ScoreLine
    expected_points: float
    exact_score_probability: float
    goal_difference_probability: float
    result_probability: float


class PredictionOptimizer:
    def __init__(self, scoring_rules: ScoringRules | None = None) -> None:
        self.scoring_rules = scoring_rules or ScoringRules()

    def expected_points_for(
        self, prediction: ScoreLine, distribution: ProbabilityDistribution
    ) -> PredictionExpectation:
        exact_probability = 0.0
        goal_difference_probability = 0.0
        result_probability = 0.0

        for outcome in distribution.outcomes:
            category = self.scoring_rules.category_for(prediction, outcome)
            if category == ScoringCategory.EXACT_SCORE:
                exact_probability += outcome.probability
            elif category == ScoringCategory.GOAL_DIFFERENCE:
                goal_difference_probability += outcome.probability
            elif category == ScoringCategory.RESULT:
                result_probability += outcome.probability

        expected_points = (
            exact_probability * self.scoring_rules.exact_score_points
            + goal_difference_probability * self.scoring_rules.goal_difference_points
            + result_probability * self.scoring_rules.result_points
        )
        return PredictionExpectation(
            scoreline=prediction,
            expected_points=expected_points,
            exact_score_probability=exact_probability,
            goal_difference_probability=goal_difference_probability,
            result_probability=result_probability,
        )

    def rank_predictions(
        self,
        distribution: ProbabilityDistribution,
        *,
        candidates: Sequence[ScoreLine] | None = None,
        max_goals: int | None = None,
    ) -> list[PredictionExpectation]:
        if candidates is not None and max_goals is not None:
            raise GameError("Use candidates or max_goals, not both.")
        if max_goals is not None:
            if max_goals < 0:
                raise GameError("max_goals cannot be negative.")
            candidates = [
                ScoreLine(home_goals, away_goals)
                for home_goals in range(max_goals + 1)
                for away_goals in range(max_goals + 1)
            ]
        if candidates is None:
            candidates = distribution.exact_scorelines

        expectations = [
            self.expected_points_for(candidate, distribution)
            for candidate in candidates
        ]
        return sorted(
            expectations,
            key=lambda item: (
                -item.expected_points,
                -item.exact_score_probability,
                item.scoreline.home_goals + item.scoreline.away_goals,
                item.scoreline.home_goals,
                item.scoreline.away_goals,
            ),
        )
