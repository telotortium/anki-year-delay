# For docs, see ../setup.py
import argparse
import json
import logging
import os
import os.path
import pdb
import random
import sys
import time
import traceback

from datetime import datetime
from itertools import islice

import requests

# Create logger that logs to standard error
logger = logging.getLogger("anki-year-delay")
# These 2 lines prevent duplicate log lines.
logger.handlers.clear()
logger.propagate = False

LEVEL_DEFAULT = logging.INFO
level = os.environ.get("ANKI_YEAR_DELAY_LOGLEVEL")
if level:
    level = level.upper()
else:
    level = LEVEL_DEFAULT
logger.setLevel(level)

# Create handler that logs to standard error
handler = logging.StreamHandler()
handler.setLevel(level)

# Create formatter and add it to the handler
formatter = logging.Formatter("[%(levelname)8s %(asctime)s - %(name)s] %(message)s")
handler.setFormatter(formatter)

# Add handler to the logger
logger.addHandler(handler)

ANKICONNECT_URL_DEFAULT = "http://localhost:8765"
ankiconnect_url = os.environ.get(
    "ANKI_YEAR_DELAY_ANKICONNECT_URL", ANKICONNECT_URL_DEFAULT
)
ANKICONNECT_VERSION = 6


def batched(iterable, n):
    "Batch data into tuples of length n. The last batch may be shorter."
    # batched('ABCDEFG', 3) --> ABC DEF G
    if n < 1:
        raise ValueError("n must be at least one")
    it = iter(iterable)
    while batch := tuple(islice(it, n)):
        yield batch


def ankiconnect_request(payload):
    payload["version"] = ANKICONNECT_VERSION
    logger.debug("payload = %s", payload)
    response = json.loads(requests.post(ankiconnect_url, json=payload, timeout=3).text)
    logger.debug("response = %s", response)
    if response["error"] is not None:
        logger.warning("payload %s had response error: %s", payload, response)
    return response


BATCH_SIZE = 50


def main():
    try:
        rc = _main()
        if rc is not None:
            sys.exit(rc)
    except Exception:
        debug = os.environ.get("ANKI_YEAR_DELAY_DEBUG", None)
        if debug and debug != "0":
            _extype, _value, tb = sys.exc_info()
            traceback.print_exc()
            pdb.post_mortem(tb)
        else:
            raise


def _main():
    parser = argparse.ArgumentParser(
        prog="anki-year-delay",
        description="Export Article notes in Anki as individual Org-mode files to a directory.",
        epilog=f"""Environment variables:

- ANKI_YEAR_DELAY_ANKICONNECT_URL: set to the URL of AnkiConnect. Default:
  {ANKICONNECT_URL_DEFAULT}
  set to "{ANKICONNECT_URL_DEFAULT}".
- ANKI_YEAR_DELAY_DEBUG: set in order to debug using PDB upon exception.
- ANKI_YEAR_DELAY_LOGLEVEL: set log level. Default: {LEVEL_DEFAULT}
""",
    )
    parser.add_argument(
        "--tag",
        type=str,
        help="Tag for notes that should be rescheduled.",
        default="anki:year-delay",
    )
    parser.add_argument(
        "--edited", type=int, help="Only examine notes modified in the past N days."
    )
    args = parser.parse_args()

    ankiconnect_request({"action": "sync"})

    # First, find notes added to Anki but not yet to Pocket and add them to
    # Pocket.
    deck_name = "Articles"
    note_type = "Pocket Article"
    response = ankiconnect_request(
        {
            "action": "findCards",
            "params": {
                # Find notes with `given_url` and `given_title` not empty, but
                # `item_id` empty.
                "query": f'"note:{note_type}" "deck:{deck_name}" "tag:{args.tag}"'
                + (f" edited:{args.edited}" if args.edited else ""),
            },
        }
    )
    card_ids = response["result"]
    logger.info(f"card_ids: {card_ids}")
    if not card_ids:
        logger.info(
            "No matching cards found - check values of flags passed to this program"
        )
        return 0
    first_card_id = card_ids[0]
    response = ankiconnect_request(
        {
            "action": "cardsInfo",
            "params": {
                "cards": [first_card_id],
            },
        }
    )
    # Card ID is just create time of card (in milliseconds since Unix epoch,
    # see
    # https://github.com/ankidroid/Anki-Android/wiki/Database-Structure#cards).
    first_card_create_time = first_card_id

    # Discover the day number corresponding to today by forgetting the first
    # card and then iterating until the first time it's graduated from the
    # "learning" queue.
    response = ankiconnect_request(
        {
            "action": "getDeckConfig",
            "params": {
                "deck": deck_name,
            },
        },
    )
    deckConfig = response["result"]
    graduating_interval = deckConfig["new"]["ints"][0]

    ankiconnect_request(
        {
            "action": "forgetCards",
            "params": {
                "cards": [first_card_id],
            },
        },
    )

    review_count = 0
    while review_count < 10:
        response = ankiconnect_request(
            {
                "action": "cardsInfo",
                "params": {
                    "cards": [first_card_id],
                },
            }
        )
        ci = response["result"]
        if ci[0]["type"] == 2:
            break
        ankiconnect_request(
            {
                "action": "answerCards",
                "params": {
                    "answers": [
                        {
                            "cardId": first_card_id,
                            "ease": 3,
                        },
                    ],
                },
            },
        )
        review_count += 1
    # else:
    #     logger.error(f"Reviewed card ID {first_card_id} {review_count} times without graduating"}
    #     return 1

    response = ankiconnect_request(
        {
            "action": "cardsInfo",
            "params": {
                "cards": [first_card_id],
            },
        }
    )
    ci = response["result"][0]
    today_day_index = ci["due"] - graduating_interval

    datetime_now = datetime.utcfromtimestamp(time.time())
    datetime_card_creation = datetime.utcfromtimestamp(first_card_create_time // 1000)
    days_since_card_creation = (datetime_now - datetime_card_creation).days
    delay_day = round(random.uniform(0.9, 1.1) * 365.0)
    delay_day_index = today_day_index - days_since_card_creation + delay_day

    for cids in batched(card_ids, BATCH_SIZE):
        logger.info(f"cids: {cids}")
        ankiconnect_request(
            {
                "action": "forgetCards",
                "params": {
                    "cards": cids,
                },
            },
        )
        for i in range(review_count):
            ankiconnect_request(
                {
                    "action": "answerCards",
                    "params": {
                        "answers": [
                            {
                                "cardId": cid,
                                "ease": 3,
                            }
                            for cid in cids
                        ],
                    },
                },
            )

        response = ankiconnect_request(
            {
                "action": "cardsInfo",
                "params": {
                    "cards": cids,
                },
            }
        )
        cis = response["result"]
        actions = []
        for ci in cis:
            cid = ci["cardId"]
            datetime_card_creation = datetime.utcfromtimestamp(cid // 1000)
            days_since_card_creation = (datetime_now - datetime_card_creation).days
            delay_day = round(random.uniform(0.9, 1.1) * 365.0)
            delay_day_index = today_day_index - days_since_card_creation + delay_day
            actions.append(
                {
                    "action": "setSpecificValueOfCard",
                    "params": {
                        "card": cid,
                        "keys": ["due"],
                        "newValues": [delay_day_index],
                    },
                }
            )
            actions.append(
                {
                    "action": "removeTags",
                    "params": {
                        "notes": [ci["note"]],
                        "tags": args.tag,
                    },
                }
            )
        ankiconnect_request(
            {
                "action": "multi",
                "params": {
                    "actions": actions,
                },
            }
        )
    ankiconnect_request({"action": "sync"})
    logger.info("Success")


if __name__ == "__main__":
    main()
