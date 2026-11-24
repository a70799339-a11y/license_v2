import json, urllib.request
TOKEN = "69e020a24dea971727388e9a62b3edfc"
def get(url):
    try:
        r = urllib.request.Request(url, headers={"Authorization": f"token {TOKEN}"})
        with urllib.request.urlopen(r, timeout=20) as resp:
            d = json.loads(resp.read())
        return resp.status, d
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]
st, d = get("https://gitee.com/api/v5/user")
print("GET /user:", st, str(d)[:100])
st, d = get("https://gitee.com/api/v5/repos/william_song/license-v2")
if isinstance(d, dict):
    print("GET repo:", st, "name=", d.get("full_name"), "has_issues=", d.get("has_issues"))
else:
    print("GET repo:", st, d)
st, d = get("https://gitee.com/api/v5/repos/william_song/license-v2/issues?state=all&per_page=3")
print("GET issues:", st, str(d)[:150])
