import asyncio
from GhostRun import GitHubProjectRunner

REPOS = [
    "owner/bot-one",
    "owner/bot-two",
    "owner/bot-three",
]

async def main():
    runners = []
    for repo in REPOS:
        r = GitHubProjectRunner(
            f"https://github.com/{repo}",
            restart_on_crash=True,
            restart_delay=10.0,
        )
        await r.load()
        runners.append(r.run())

    await asyncio.gather(*runners)

asyncio.run(main())