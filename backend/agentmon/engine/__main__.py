import asyncio
import logging

from agentmon.engine.app import main

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
asyncio.run(main())
