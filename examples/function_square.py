from grape import Grader, Submission, case

grader = Grader(id="square", total_points=2)
with grader.criterion("correctness", points=2) as c:
    c.function("square").cases(
        case(2).returns(4),
        case(-3).returns(9),
    )

submission = Submission.from_source(
    """
def square(value: int) -> int:
    return value * value
"""
)

result = grader.grade(submission)
print(result.score.earned, result.score.possible)
