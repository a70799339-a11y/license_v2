//! 跨语言验签示例：验证服务端（Python）签发、客户端（Rust）验签的链路
//! 用法：cargo run -p license-core --example cross_verify <signed_license.json>
//! 其中 JSON 为 /api/client/activate 返回的 signed_license 字段
use license_core::verify_license;
use serde_json::Value;

fn main() {
    let path = std::env::args().nth(1).expect("用法: cross_verify <signed_license.json>");
    let raw = std::fs::read_to_string(&path).expect("读取文件失败");
    let v: Value = serde_json::from_str(&raw).expect("JSON 解析失败");
    let payload = v.get("payload").expect("缺少 payload");
    let sig = v.get("signature").and_then(|x| x.as_str()).expect("缺少 signature");
    let pub_key = v.get("public_key").and_then(|x| x.as_str()).expect("缺少 public_key");
    if verify_license(payload, sig, pub_key) {
        println!("CROSS-VERIFY-OK: 服务端签名在 Rust 侧验签通过");
    } else {
        eprintln!("CROSS-VERIFY-FAIL: 验签失败（规范化 JSON 可能不一致）");
        std::process::exit(1);
    }
}
