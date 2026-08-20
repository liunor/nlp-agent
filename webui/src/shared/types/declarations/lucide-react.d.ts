// 类型垫片：lucide-react@0.469.0 不发布 .d.ts 类型声明（package.json 无 types/exports 字段，
// dist 下无 .d.ts），仓库亦未依赖 @types/lucide-react。声明为 any 以消除 TS7016
// （Could not find a declaration file for module 'lucide-react'）。
// 注：此为仓库既有依赖问题的权宜方案，待 lucide-react 升级至自带类型版本后可移除。
declare module "lucide-react";
