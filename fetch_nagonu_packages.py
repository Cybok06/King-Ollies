"""Read Nagonu packages only; never submits orders or changes database prices."""
import json
from pathlib import Path
import requests


def fetch_packages(api_key):
    response = requests.get('https://nagonu.site/api/packages.php',
                            headers={'x-api-key': api_key}, timeout=30, allow_redirects=False)
    if response.status_code != 200:
        raise ValueError(f'Package endpoint returned HTTP {response.status_code}.')
    payload = response.json()
    if payload.get('status') not in ('success', 200, '200'):
        raise ValueError('The API did not report a successful package response.')
    groups = payload.get('data', {}).get('data', {})
    if not isinstance(groups, dict) or not any(key in groups for key in ('regular_packages', 'bigtime_packages')):
        raise ValueError('Unexpected package response format; inspect the provider documentation.')
    packages = []
    for group in ('regular_packages', 'bigtime_packages'):
        for item in groups.get(group, []):
            packages.append({'category': group, **{key: item[key] for key in
                ('service_name', 'network', 'offer_id', 'package', 'amount')}})
    return packages


if __name__ == '__main__':
    key = 'NAGONU_X_EsolQUmPFtlw4kg-jzPP1Bdri4zJzP'
    try:
        packages = fetch_packages(key)
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SystemExit(f'Could not fetch packages: {exc}') from None
    destination = Path('artifacts/nagonu/packages.json')
    destination.parent.mkdir(parents=True, exist_ok=True)
    formatted_json = json.dumps(packages, indent=2)
    destination.write_text(formatted_json, encoding='utf-8')
    print(formatted_json)
