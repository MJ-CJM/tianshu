#!/usr/bin/env python3
"""
批红流程测试脚本

这个脚本演示了Tianshu平台的批红流程测试。
批红通常指文档审阅、标记修改、审批流程等操作。
"""

import asyncio
import json
from pathlib import Path

# 模拟一个简单的批红流程
class RedlineProcessor:
    """批红处理器"""
    
    def __init__(self):
        self.document = ""
        self.redlines = []
        self.approvals = []
    
    async def load_document(self, filepath: str) -> str:
        """加载文档"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                self.document = f.read()
            return f"文档已加载: {filepath}, 长度: {len(self.document)} 字符"
        except Exception as e:
            return f"加载文档失败: {str(e)}"
    
    async def add_redline(self, position: int, text: str, comment: str = "") -> str:
        """添加批红标记"""
        redline = {
            "id": len(self.redlines) + 1,
            "position": position,
            "text": text,
            "comment": comment,
            "status": "pending"
        }
        self.redlines.append(redline)
        return f"批红已添加: ID={redline['id']}, 位置={position}, 文本='{text[:50]}...'"
    
    async def review_redlines(self) -> str:
        """审阅所有批红"""
        if not self.redlines:
            return "没有待审阅的批红"
        
        result = ["批红审阅报告:", "=" * 40]
        for r in self.redlines:
            result.append(f"ID: {r['id']}")
            result.append(f"位置: {r['position']}")
            result.append(f"文本: {r['text'][:100]}...")
            result.append(f"批注: {r['comment']}")
            result.append(f"状态: {r['status']}")
            result.append("-" * 20)
        
        return "\n".join(result)
    
    async def approve_redline(self, redline_id: int, approver: str, notes: str = "") -> str:
        """批准批红"""
        for r in self.redlines:
            if r["id"] == redline_id:
                r["status"] = "approved"
                approval = {
                    "redline_id": redline_id,
                    "approver": approver,
                    "notes": notes,
                    "timestamp": asyncio.get_event_loop().time()
                }
                self.approvals.append(approval)
                return f"批红 ID={redline_id} 已由 {approver} 批准"
        
        return f"未找到批红 ID={redline_id}"
    
    async def generate_final_document(self) -> str:
        """生成最终文档"""
        if not self.redlines:
            return self.document
        
        # 简单的批红应用逻辑
        doc_chars = list(self.document)
        applied_redlines = []
        
        for r in sorted(self.redlines, key=lambda x: x["position"], reverse=True):
            if r["status"] == "approved":
                position = r["position"]
                if 0 <= position < len(doc_chars):
                    # 在指定位置插入批红文本
                    insertion = f"[批红{r['id']}: {r['text']}]"
                    doc_chars.insert(position, insertion)
                    applied_redlines.append(r["id"])
        
        final_doc = "".join(doc_chars)
        
        summary = f"最终文档生成完成:\n"
        summary += f"- 原始长度: {len(self.document)} 字符\n"
        summary += f"- 最终长度: {len(final_doc)} 字符\n"
        summary += f"- 应用的批红: {applied_redlines}\n"
        summary += f"- 未应用的批红: {[r['id'] for r in self.redlines if r['status'] != 'approved']}"
        
        return summary + "\n\n" + final_doc[:500] + "..." if len(final_doc) > 500 else final_doc
    
    async def export_report(self, output_path: str) -> str:
        """导出批红报告"""
        report = {
            "document_length": len(self.document),
            "redlines_count": len(self.redlines),
            "redlines": self.redlines,
            "approvals_count": len(self.approvals),
            "approvals": self.approvals,
            "summary": {
                "approved": len([r for r in self.redlines if r["status"] == "approved"]),
                "pending": len([r for r in self.redlines if r["status"] == "pending"]),
                "rejected": len([r for r in self.redlines if r["status"] == "rejected"])
            }
        }
        
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            return f"报告已导出到: {output_path}"
        except Exception as e:
            return f"导出报告失败: {str(e)}"


async def test_redline_workflow():
    """测试批红工作流程"""
    print("开始批红流程测试")
    print("=" * 60)
    
    # 创建批红处理器
    processor = RedlineProcessor()
    
    # 1. 创建测试文档
    test_doc = """这是一个测试文档，用于演示批红流程。

第一章：项目概述
本项目旨在开发一个智能文档处理系统，支持自动批红、审阅和版本控制。

第二章：技术要求
系统需要支持以下功能：
1. 文档加载和解析
2. 批红标记添加
3. 多级审批流程
4. 版本历史管理

第三章：实施计划
项目分为三个阶段：
- 第一阶段：基础框架搭建
- 第二阶段：核心功能开发
- 第三阶段：测试和部署

结束语：
感谢参与本次测试，期待您的批红意见。"""
    
    # 保存测试文档
    doc_path = "test_redline/test_document.txt"
    Path(doc_path).parent.mkdir(parents=True, exist_ok=True)
    with open(doc_path, 'w', encoding='utf-8') as f:
        f.write(test_doc)
    
    print("1. 加载文档...")
    result = await processor.load_document(doc_path)
    print(result)
    print()
    
    # 2. 添加批红
    print("2. 添加批红标记...")
    redlines = [
        (50, "智能文档审阅系统", "建议使用更具体的名称"),
        (120, "自动批红、审阅、版本控制和协作", "建议增加协作功能"),
        (200, "系统需要支持以下核心功能：", "建议分类列出功能"),
        (280, "项目分为四个阶段：", "建议增加需求分析阶段"),
    ]
    
    for pos, text, comment in redlines:
        result = await processor.add_redline(pos, text, comment)
        print(f"  - {result}")
    print()
    
    # 3. 审阅批红
    print("3. 审阅批红...")
    result = await processor.review_redlines()
    print(result)
    print()
    
    # 4. 批准批红
    print("4. 批准批红...")
    approvals = [
        (1, "张三", "同意修改"),
        (2, "李四", "建议明确协作功能范围"),
        (4, "王五", "同意增加需求分析阶段"),
    ]
    
    for redline_id, approver, notes in approvals:
        result = await processor.approve_redline(redline_id, approver, notes)
        print(f"  - {result}")
    print()
    
    # 5. 生成最终文档
    print("5. 生成最终文档...")
    result = await processor.generate_final_document()
    print(result)
    print()
    
    # 6. 导出报告
    print("6. 导出批红报告...")
    report_path = "test_redline/redline_report.json"
    result = await processor.export_report(report_path)
    print(result)
    
    # 7. 显示报告摘要
    print("\n7. 批红流程测试完成")
    print("=" * 60)
    print("测试总结:")
    print(f"- 测试文档: {doc_path}")
    print(f"- 批红数量: {len(processor.redlines)}")
    print(f"- 批准数量: {len([r for r in processor.redlines if r['status'] == 'approved'])}")
    print(f"- 报告文件: {report_path}")
    
    # 清理
    if Path(doc_path).exists():
        Path(doc_path).unlink()
    
    return True


if __name__ == "__main__":
    # 运行测试
    success = asyncio.run(test_redline_workflow())
    
    if success:
        print("\n✅ 批红流程测试成功完成！")
        print("测试文件已生成在 test_redline/ 目录中。")
    else:
        print("\n❌ 批红流程测试失败！")