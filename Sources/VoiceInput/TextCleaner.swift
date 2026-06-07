import Foundation

/// 基础文本清洗：MVP 阶段只做简单处理
/// 后续可替换为 LLM 增强
enum TextCleaner {
    static func basicCleanup(_ text: String) -> String {
        var result = text
        // 去掉 ASR 常见口头禅
        let fillers = ["嗯", "啊", "呃", "那个", "这个", "就是说", "然后"]
        for filler in fillers {
            result = result.replacingOccurrences(of: filler, with: "")
        }
        // 多余空白
        result = result.replacingOccurrences(
            of: "\\s+",
            with: " ",
            options: .regularExpression
        )
        return result.trimmingCharacters(in: .whitespaces)
    }
}
