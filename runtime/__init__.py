"""
Lina Runtime — Production-grade LLM execution layer.

Модули:
  - safety_guard:       Фильтрация prompt injection, опасных команд
  - output_cleaner:     Очистка LLM-ответов от утечек и маркеров
  - prompt_builder:     Безопасная сборка промпта (SYSTEM/CONTEXT/HISTORY/USER)
  - response_pipeline:  Централизованный pipeline обработки ответов
  - tool_executor:      Structured tool execution (JSON schema)
  - model_manager:      Единый менеджер моделей с sticky switching
  - conversation_state: Управление состоянием диалога
  - rag_layer:          Безопасная RAG-прослойка

Архитектурный поток:
  User → SafetyGuard → PromptBuilder → LLM → ResponsePipeline
       → ToolExecutor (если JSON) → OutputCleaner → Final output
"""

__version__ = "0.8.0"
