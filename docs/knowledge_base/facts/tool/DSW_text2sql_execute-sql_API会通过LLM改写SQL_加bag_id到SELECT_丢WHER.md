---
category: tool
tags: DSW,text2sql,API,workaround
---

DSW text2sql execute-sql API会通过LLM改写SQL(加bag_id到SELECT、丢WHERE条件、加LIMIT 100)，不适合精确查询。绕过方式：直接用duckdb读parquet文件。SSH传Python脚本必须用SCP文件方式，inline Python因shell转义(花括号/引号)必失败。
