---
category: infra
tags: qoder,DSW,vite,dev-login,掩码代理,评测页面,公司防火墙
---

[src=qoder:DataMining-前端Web测试入口-掩码代理dev-login] DataMining 前端 Web 测试入口（掩码代理+dev-login）
用户公司电脑有防火墙（按 MAC 全局禁止），直接访问 ALB SSO 会被拦；解决方案是 DSW 上 vite 做掩码代理，浏览器只访问 http://8.130.209.216:31684：

1. /root/data/data-platform-fe/vite.config.js 加了 /api 与 /cerberus 代理 → ALB（alb-2hjgj3j3kmcpx75nds.cn-wulanchabu.alb.aliyuncsslb.com），/datamining → 本地后端 8089；并内置 /dev-login 插件（GET 返回登录表单，POST 服务端对密码做 MD5 后转发 ALB /api/cerberus/auth/login，取 data.tokenValue 存 localStorage('token')）。
2. .env.development.local 各 VITE_* 指向 /api/*、/cerberus 等掩码路径；VITE_DATAMINING_API_BASE_URL 未设置走 /datamining 本地代理。
3. 用户测试流程：打开 http://8.130.209.216:31684/dev-login 登录（cerberus 账号，密码服务端 MD5）→ 自动跳转 /dm/ → 算法评测页 /dm/flow-replay/evaluation 可测文生SQL。
4. dev-bypass-token 只对本地后端有效，走 ALB 的接口必须真实登录 token。
5. vite dev 若需重启：cd /root/data/data-platform-fe && nohup npm run dev 拉起（端口 5174 内部，31684 为对外映射）。
