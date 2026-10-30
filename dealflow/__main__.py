import argparse
from .core import run

def main():
    p = argparse.ArgumentParser(description="Offline Ananda dealflow demo")
    p.add_argument("command", choices=["run"]); p.add_argument("folder")
    p.add_argument("--db", required=True); p.add_argument("--digest", required=True)
    a = p.parse_args(); good, review = run(a.folder, a.db, a.digest)
    print(f"processed {good} deals; queued {review} for human review")
if __name__ == "__main__": main()

