import json, urllib.request, urllib.parse, urllib.error
TOKEN = "69e020a24dea971727388e9a62b3edfc"
def try_post(url, params, desc):
    data = urllib.parse.urlencode(params).encode()
    r = urllib.request.Request(url, data=data, method="POST")
    r.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return desc, resp.status, json.loads(resp.read()).get("number")
    except urllib.error.HTTPError as e:
        return desc, e.code, e.read().decode()[:120]

print(*try_post(f"https://gitee.com/api/v5/repos/william_song/license-v2/issues?access_token={TOKEN}",
                {"title": "[探针] query-param 认证测试", "body": "probe"}, "query-param"), sep=" | ")
print(*try_post("https://gitee.com/api/v5/repos/william_song/license-v2/issues",
                {"access_token": TOKEN, "title": "[探针] body-token 测试", "body": "probe"}, "body-token"), sep=" | ")
