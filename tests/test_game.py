from __future__ import annotations

import unittest

from kick_tipp_tipper.game import (
    ActualOutcome,
    MatchResult,
    PredictionOptimizer,
    ProbabilityDistribution,
    ScoreLine,
    ScoringCategory,
    ScoringRules,
)


class ScoringRulesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = ScoringRules()

    def test_exact_score_scores_four_points(self) -> None:
        prediction = ScoreLine(2, 1)
        actual = ActualOutcome.exact(ScoreLine(2, 1), 1.0)

        self.assertEqual(self.rules.category_for(prediction, actual), ScoringCategory.EXACT_SCORE)
        self.assertEqual(self.rules.points_for(prediction, actual), 4)

    def test_non_draw_goal_difference_scores_three_points(self) -> None:
        prediction = ScoreLine(2, 1)
        actual = ActualOutcome.exact(ScoreLine(3, 2), 1.0)

        self.assertEqual(self.rules.category_for(prediction, actual), ScoringCategory.GOAL_DIFFERENCE)
        self.assertEqual(self.rules.points_for(prediction, actual), 3)

    def test_draw_goal_difference_falls_back_to_result_points(self) -> None:
        prediction = ScoreLine(1, 1)
        actual = ActualOutcome.exact(ScoreLine(2, 2), 1.0)

        self.assertEqual(self.rules.category_for(prediction, actual), ScoringCategory.RESULT)
        self.assertEqual(self.rules.points_for(prediction, actual), 2)

    def test_wrong_result_scores_zero(self) -> None:
        prediction = ScoreLine(2, 0)
        actual = ActualOutcome.exact(ScoreLine(0, 1), 1.0)

        self.assertEqual(self.rules.category_for(prediction, actual), ScoringCategory.NONE)
        self.assertEqual(self.rules.points_for(prediction, actual), 0)


class PredictionOptimizerTest(unittest.TestCase):
    def test_expected_points_include_exact_difference_and_known_other_buckets(self) -> None:
        distribution = ProbabilityDistribution(
            (
                ActualOutcome.exact(ScoreLine(1, 0), 0.20),
                ActualOutcome.exact(ScoreLine(2, 1), 0.10),
                ActualOutcome.exact(ScoreLine(0, 0), 0.15),
                ActualOutcome.exact(ScoreLine(1, 1), 0.05),
                ActualOutcome.bucket("Any Other Home Win", 0.10, result=MatchResult.HOME_WIN),
                ActualOutcome.bucket("Any Other Score", 0.40),
            )
        )

        expectation = PredictionOptimizer().expected_points_for(
            ScoreLine(1, 0),
            distribution,
        )

        self.assertAlmostEqual(expectation.exact_score_probability, 0.20)
        self.assertAlmostEqual(expectation.goal_difference_probability, 0.10)
        self.assertAlmostEqual(expectation.result_probability, 0.10)
        self.assertAlmostEqual(expectation.expected_points, 1.30)

    def test_rank_predictions_orders_by_expected_points(self) -> None:
        distribution = ProbabilityDistribution(
            (
                ActualOutcome.exact(ScoreLine(1, 0), 0.40),
                ActualOutcome.exact(ScoreLine(0, 0), 0.20),
                ActualOutcome.exact(ScoreLine(0, 1), 0.40),
            )
        )

        rankings = PredictionOptimizer().rank_predictions(distribution)

        self.assertEqual(rankings[0].scoreline, ScoreLine(0, 1))
        self.assertGreaterEqual(rankings[0].expected_points, rankings[1].expected_points)


if __name__ == "__main__":
    unittest.main()

