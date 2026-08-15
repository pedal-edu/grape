from grape import Grader, Submission

grader = Grader(id="hello-world", total_points=1)
with grader.criterion("program", points=1) as c:
    c.output.expect.equals("Hello, world!\n")

submission = Submission.from_source('print("Hello, world!")\n')
result = grader.grade(submission)
print(result.score.earned, result.score.possible)
