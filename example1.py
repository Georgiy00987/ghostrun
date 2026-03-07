import asyncio
from GhostRun import GitHubProjectRunner

async def main():
    runner = GitHubProjectRunner(
        "https://github.com/owner/repo",
        token="ghp_...",        # необязательно
        restart_on_crash=True,
        restart_delay=15.0,
    )
    await runner.load()
    await runner.run()

asyncio.run(main())