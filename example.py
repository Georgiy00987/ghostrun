from dotenv import load_dotenv # TODO: Termux import .env file
load_dotenv()

import asyncio
import logging
import os

from github_runner import GitHubProjectRunner

logging.basicConfig(
    format="[%(levelname)-5s] [%(asctime)s] [LINE:%(lineno)d] %(name)s — %(message)s",
    datefmt="%H:%M",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN") # Github токен (необезателен) 

async def main(contents: list[str]) -> None:
    runners = []

    for repo in contents:
        logger.info("─" * 60)

        git_url = f"https://github.com/{repo}"
        github = GitHubProjectRunner(
            git_url,
            token=GITHUB_TOKEN,
            restart_on_crash=True,   # перезапускать бота при падении
            restart_delay=15.0,
        )
        await github.load()
        runners.append(github.run())

    print()
    logger.info(f"Загружено {len(runners)} проектов, запускаю...\n")
    await asyncio.gather(*runners) # Запуск


if __name__ == "__main__":
    asyncio.run(
        main([ # owner/repo формат github
            ""
        ])
    )
