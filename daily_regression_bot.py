import base64
import os
import sys
from datetime import datetime

import requests

BASE_URL = "https://bibip.testexecutor.com"
PROJECT_ID = 1
PROJECT_KEY = "ZTP"

# Используем 16126 как точку отсчёта,
# чтобы получить последние запуски.
PASS_RATES_URL = (
    f"{BASE_URL}/api/reporting/v1/launches/16126/pass-rates"
)

RUNS_BEFORE = 5
RUNS_AFTER = 5
TIMEOUT = 30

TEST_EXECUTOR_TOKEN = os.getenv("TEST_EXECUTOR_TOKEN")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

if not TEST_EXECUTOR_TOKEN:
    print("ERROR: TEST_EXECUTOR_TOKEN is not set")
    sys.exit(1)

if not SLACK_WEBHOOK_URL:
    print("ERROR: SLACK_WEBHOOK_URL is not set")
    sys.exit(1)

# Кодируем токен в формат Basic Auth (токен используется вместо пароля, логин пустой)
raw_auth_string = f":{TEST_EXECUTOR_TOKEN}"
encoded_auth_string = base64.b64encode(raw_auth_string.encode("utf-8")).decode("utf-8")

HEADERS = {
    "Authorization": f"Basic {encoded_auth_string}",
    "Accept": "application/json",
}

def get_json(url, params=None):
    response = requests.get(
        url,
        headers=HEADERS,
        params=params,
        timeout=TIMEOUT,
    )

    response.raise_for_status()
    return response.json()

def get_latest_launch_id():
    params = {
        "runsBefore": RUNS_BEFORE,
        "runsAfter": RUNS_AFTER,
        "projectId": PROJECT_ID,
    }

    data = get_json(PASS_RATES_URL, params)
    items = data.get("items", [])

    if not items:
        raise RuntimeError("No launches found")

    launch_ids = [
        item["launchId"]
        for item in items
        if item.get("launchId") is not None
    ]

    if not launch_ids:
        raise RuntimeError("No launchId found")

    latest_launch_id = max(launch_ids)

    print(f"Latest launch ID: {latest_launch_id}")

    return latest_launch_id

def get_launch_summary(launch_id):
    url = f"{BASE_URL}/api/reporting/v1/launches/{launch_id}"

    params = {
        "projectId": PROJECT_ID,
    }

    data = get_json(url, params)

    return data["data"]

def get_failed_tests(launch_id):
    url = f"{BASE_URL}/api/reporting/v1/launches/{launch_id}/tests"

    params = {
        "projectId": PROJECT_ID,
    }

    data = get_json(url, params)

    items = data.get("items", [])

    failed_tests = []

    for test in items:
        if test.get("status") == "FAILED":
            name = test.get("name")
            test_id = test.get("id")

            if name:
                failed_tests.append({
                    "name": name,
                    "id": test_id,
                })

    return failed_tests

def build_test_url(test_id):
    """
    Формируем ссылку на тест.
    """
    if not test_id:
        return None

    return f"{BASE_URL}/projects/{PROJECT_KEY}/tests/{test_id}"

def build_launch_url(launch_id):
    """
    Ссылка на конкретный launch.
    """
    return (
        f"{BASE_URL}/projects/{PROJECT_KEY}/"
        f"executions/automation-launchers/60/124/{launch_id}"
    )

def build_slack_message(summary, failed_tests, launch_id):
    passed = int(summary.get("passed", 0) or 0)
    failed = int(summary.get("failed", 0) or 0)
    skipped = int(summary.get("skipped", 0) or 0)

    total = passed + failed + skipped

    if total > 0:
        pass_rate = round((passed / total) * 100)
    else:
        pass_rate = 0

    emoji = "🟢" if failed == 0 else "🔴"

    started_at = summary.get("startedAt")

    if started_at:
        try:
            date = datetime.fromisoformat(
                started_at.replace("Z", "+00:00")
            )
            date_str = date.strftime("%b %-d")
        except ValueError:
            date_str = started_at[:10]
    else:
        date_str = datetime.now().strftime("%b %-d")

    launch_url = build_launch_url(launch_id)

    message = (
        f"🧪 *Daily Regression — {date_str}*\n\n"
        f"*Overall:* {emoji} {pass_rate}% passed\n\n"
        f"• Total: {total}\n"
        f"• ✅ Passed: {passed}\n"
        f"• ❌ Failed: {failed}\n"
        f"• ⏭️ Skipped: {skipped}\n"
    )

    if failed_tests:
        message += "\n*Failed tests:*\n"

        for test in failed_tests:
            test_name = test["name"]
            test_id = test.get("id")

            test_url = build_test_url(test_id)

            if test_url:
                message += f"• <{test_url}|{test_name}>\n"
            else:
                message += f"• {test_name}\n"

    message += f"\n🔗 <{launch_url}|Open launch>"

    return message

def send_to_slack(message):
    payload = {
        "text": message
    }

    response = requests.post(
        SLACK_WEBHOOK_URL,
        json=payload,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    print("Slack message sent successfully")

def main():
    print("Starting Daily Regression bot...")

    launch_id = get_latest_launch_id()

    summary = get_launch_summary(launch_id)

    print(
        f"Launch {launch_id}: "
        f"passed={summary.get('passed')}, "
        f"failed={summary.get('failed')}, "
        f"skipped={summary.get('skipped')}"
    )

    failed_tests = get_failed_tests(launch_id)

    print(f"Failed tests: {len(failed_tests)}")

    message = build_slack_message(
        summary,
        failed_tests,
        launch_id,
    )

    print("\nMessage:")
    print("----------------------------------------")
    print(message)
    print("----------------------------------------")

    send_to_slack(message)

if __name__ == "__main__":
    try:
        main()

    except requests.HTTPError as e:
        print(f"HTTP ERROR: {e}")

        if e.response is not None:
            print(f"Status: {e.response.status_code}")
            print(f"Response: {e.response.text[:1000]}")

        sys.exit(1)

    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)
