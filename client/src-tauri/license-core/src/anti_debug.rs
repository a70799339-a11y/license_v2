//! 反调试 / 反篡改检测
//!
//! 商业化加固第一层：进程被调试器附加时拒绝执行验签与激活。
//! 后续可叠加：代码完整性自校验、VMProtect 类加壳（见 docs/02-安全加固指南.md）。

/// 综合检测：本地调试器 / 远程附加 / 调试端口 / 时间侧信道
#[cfg(windows)]
pub fn is_debugger_present() -> bool {
    win::detect()
}

/// 非 Windows 平台占位（开发/CI 用）
#[cfg(not(windows))]
pub fn is_debugger_present() -> bool {
    false
}

#[cfg(windows)]
mod win {
    use std::ffi::c_void;

    #[link(name = "kernel32")]
    extern "system" {
        fn IsDebuggerPresent() -> i32;
        fn CheckRemoteDebuggerPresent(h_process: *mut c_void, is_debugger: *mut i32) -> i32;
        fn GetCurrentProcess() -> *mut c_void;
        fn QueryPerformanceCounter(lp: *mut i64) -> i32;
    }

    #[link(name = "ntdll")]
    extern "system" {
        // ProcessDebugPort = 7：被调试时返回非 0
        fn NtQueryInformationProcess(
            h_process: *mut c_void,
            info_class: u32,
            info: *mut usize,
            info_len: u32,
            ret_len: *mut u32,
        ) -> i32;
    }

    pub fn detect() -> bool {
        unsafe {
            if IsDebuggerPresent() != 0 {
                return true;
            }
            let mut remote = 0i32;
            if CheckRemoteDebuggerPresent(GetCurrentProcess(), &mut remote) != 0 && remote != 0 {
                return true;
            }
            let mut debug_port: usize = 0;
            let status = NtQueryInformationProcess(
                GetCurrentProcess(),
                7,
                &mut debug_port,
                core::mem::size_of::<usize>() as u32,
                core::ptr::null_mut(),
            );
            if status == 0 && debug_port != 0 {
                return true;
            }
            timing_anomaly()
        }
    }

    /// 空操作计时：正常执行微秒级；被断点/单步介入会放大两个数量级
    fn timing_anomaly() -> bool {
        unsafe {
            let mut t0: i64 = 0;
            let mut t1: i64 = 0;
            QueryPerformanceCounter(&mut t0);
            let mut acc: u64 = 0;
            for i in 0..20_000u64 {
                acc = acc.wrapping_add(i.wrapping_mul(31).rotate_left(7));
            }
            core::ptr::write_volatile(&mut acc as *mut u64, acc);
            QueryPerformanceCounter(&mut t1);
            (t1 - t0) > 50_000
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_debugger_in_test_env() {
        // 测试环境不应触发反调试（CI/开发机无附加调试器）
        assert!(!is_debugger_present());
    }
}
