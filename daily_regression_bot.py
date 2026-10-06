import base64
import os
import sys
from datetime import datetime

import requests

BASE_URL = "https://bibip.testexecutor.com"
PROJECT_ID = 1
PROJECT_KEY = "ZTP"

# Изменено: используем эндпоинт общего списка запусков проекта,
# чтобы всегда видеть самые последние прогоны.
LAUNCHES_URL = f"{BASE_URL}/api/reporting/v1/launches"

TIMEOUT = 30

TEST_EXECUTOR_TOKEN = os.getenv("TEST_EXECUTOR_TOKEN")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

if not TEST_EXECUTOR_TOKEN:
    print("ERROR: TEST_EXECUTOR_TOKEN is not set")
    sys.exit(1)

if not SLACK_WEBHOOK_URL:
    print("ERROR: SLACK_WEBHOOK_URL is not set")
    sys.exit(1)


def get_authenticated_headers():
    """
    Обменивает постоянный Access Token на временный сессионный JWT-токен.
    """
    print("Authenticating with Access Token...")
    refresh_url = f"{BASE_URL}/api/iam/v1/auth/refresh"
    payload = {"refreshToken": TEST_EXECUTOR_TOKEN}
    
    try:
        response = requests.post(refresh_url, json=payload, timeout=TIMEOUT)
        response.raise_for_status()
        
        data = response.json()
        token_type = data.get("authTokenType", "Bearer")
        access_token = data.get("authToken")
        
        print("Authentication successful!")
        return {
            "Authorization": f"{token_type} {access_token}",
            "Accept": "application/json",
        }
    except Exception as e:
        print(f"AUTHENTICATION ERROR: Failed to exchange access token. Details: {e}")
        sys.exit(1)


# Получаем заголовки с валидной рабочей сессией
HEADERS = get_authenticated_headers()


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
    """
    Запрашивает последние запуски проекта и возвращает ID самого свежего из них.
    """
    params = {
        "projectId": PROJECT_ID,
        "pageSize": 10,  # Берем последние 10 прогонов для анализа
        "sort": "id,desc"  # Сортируем от самых новых к старым
    }

    data = get_json(LAUNCHES_URL, params)
    items = data.get("items", [])

    if not items:
        # Если эндпоинт со списками пуст или имеет другую структуру,
        # попробуем поискать в поле "data"
        items = data.get("data", [])

    if not items:
        raise RuntimeError("No launches found in project")

    # Ищем самый свежий завершенный запуск
    for item in items:
        launch_id = item.get("id") or item.get("launchId")
        status = item.get("status")
        
        # Пропускаем запуски, которые еще выполняются (IN_PROGRESS)
        if launch_id is not None and status != "IN_PROGRESS":
            print(f"Found latest completed launch ID: {launch_id} (Status: {status})")
            return launch_id

    # Если все 10 прогонов выполняются, берем просто самый первый
    first_item = items[0]
    launch_id = first_item.get("id") or first_item.get("launchId")
    print(f"Forced latest launch ID: {launch_id}")
    return launch_id


def get_launch_summary(launch_id):
    url = f"{BASE_URL}/api/reporting/v1/launches/{launch_id}"
    params = {"projectId": PROJECT_ID}
    data = get_json(url, params)
    return data.get("data") or data


def get_failed_tests(launch_id):
    url = f"{BASE_URL}/api/reporting/v1/launches/{launch_id}/tests"
    params = {"projectId": PROJECT_ID}
    data = get_json(url, params)
    
    items = data.get("items", [])
    if not items:
        items = data.get("data", [])
        
    failed_tests = []
    for test in items:
        if test.get("status") == "FAILED":
            name = test.get("name")
            test_id = test.get("id")
            if name:
                failed_tests.append({"name": name, "id": test_id})
    return failed_tests


def build_test_url(test_id):
    if not test_id:
        return None
    return f"{BASE_URL}/projects/{PROJECT_KEY}/test-runs/{test_id}"


def build_launch_url(launch_id):
    return f"{BASE_URL}/projects/{PROJECT_KEY}/launches/{launch_id}"


def build_slack_message(summary, failed_tests, launch_id):
    passed = int(summary.get("passed", 0) or 0)
    failed = int(summary.get("failed", 0) or 0)
    skipped = int(summary.get("skipped", 0) or 0)

    total = passed + failed + skipped
    pass_rate = round((passed / total) * 100) if total > 0 else 0
    emoji = "🟢" if failed == 0 else "🔴"

    started_at = summary.get("startedAt")
    if started_at:
        try:
            date = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            date_str = date.strftime("%b %-d")
        except ValueError:
            date_str = started_at[:10]
    else:
        date_str = datetime.now().strftime("%b %-d")

    launch_url = build_launch_url(launch_id)

    message = (
        f"🧪 *PhotonBot — {date_str}*\n\n"
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
        "text": message,
        "username": "PhotonBot",
        "icon_emoji": ":test_tube:"
    }
    response = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=TIMEOUT)
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

    message = build_slack_message(summary, failed_tests, launch_id)
    print("\nMessage sent to Slack.")
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
