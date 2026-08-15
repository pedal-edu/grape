from grape import Grader, Submission, case

grader = Grader(id="fahrenheit-loop", total_points=4)

with grader.criterion("function", points=2) as c:
    c.function("convert_fahrenheit").signature(parameters=[int], returns=float)
    c.function("convert_fahrenheit").cases(
        case(10).returns(50.0),
        case(20).returns(68.0),
        case(30).returns(86.0),
    )

with grader.criterion("structure", points=1) as c:
    loop = c.source.require.for_loop(count=1)
    c.source.forbid.while_loop()
    c.source.forbid.literal([50.0, 68.0, 86.0])
    loop.body.require.call("convert_fahrenheit")
    loop.body.require.call("print")

with grader.criterion("program", points=1) as c:
    c.output.expect.equals("50.0\n68.0\n86.0\n")

submission = Submission.from_source(
    """
def convert_fahrenheit(value: int) -> float:
    return value * 9 / 5 + 32

for temperature in [10, 20, 30]:
    print(convert_fahrenheit(temperature))
"""
)

result = grader.grade(submission)
print(result.score.earned)
print(result.score.possible)
print(result.criteria)
print(result.feedback)
