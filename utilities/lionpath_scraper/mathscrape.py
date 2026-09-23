from lionpath import LionPathSession, Query, run_queries
from lionpath.csvout import write_csv
from lionpath.detail import enrich

# Choose semester
semester = input("Enter the semester: ")

splits = semester.lower().split(" ")
sem_code = ""

match splits[0]:
    case "spring":
        sem_code = "sp"
    case "summer":
        sem_code = "su"
    case "fall":
        sem_code = "fa"
    case _:
        sem_code = splits[0]

sem_code += str(int(splits[1]) % 100)

# create a session
session = LionPathSession()

# run lower-division queries
result_lower = run_queries(
    session,
    [
        Query(
            term=semester,
            subject=["MATH"],
            campus=["University Park"],
            course_level=["000-099", "100-199", "200-299"],
        ),
    ],
)

# run upper-division queries
result_upper = run_queries(
    session,
    [
        Query(
            term=semester,
            subject=["MATH"],
            campus=["University Park"],
            course_level=["300-399", "400-499"],
        ),
    ],
)

# run grad queries
result_grad = run_queries(
    session,
    [
        Query(
            term=semester,
            subject=["MATH"],
            campus=["University Park"],
            course_level=["500-599"],
        ),
    ],
)

# get instructors
enrich(session, result_lower.rows)
enrich(session, result_upper.rows)
enrich(session, result_grad.rows)

# write files
write_csv(result_lower.rows, sem_code + "_lower.csv")
write_csv(result_upper.rows, sem_code + "_upper.csv")
write_csv(result_grad.rows, sem_code + "_grad.csv")
