import json, urllib.request, urllib.parse, urllib.error
TOKEN = "69e020a24dea971727388e9a62b3edfc"
def post(url, params):
    data = urllib.parse.urlencode(params).encode()
    r = urllib.request.Request(url, data=data, method="POST")
    r.add_header("Authorization", f"token {TOKEN}")
    r.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]

# 探针 issue（建成功就删/关）
st, d = post("https://gitee.com/api/v5/repos/william_song/license-v2/issues",
             {"title": "[探针] 建 issue 连通性测试（稍后关闭）", "body": "probe"})
print("POST /issues:", st, str(d)[:220])
