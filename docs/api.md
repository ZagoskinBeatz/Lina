# Lina Internal API

## Core Pipeline

```python
from lina.core.main_pipeline import UnifiedPipeline, PipelineRequest

pipeline = UnifiedPipeline()
result = pipeline.process(PipelineRequest(text="Как обновить систему?"))
print(result.response)
```

## RAG

```python
from lina.rag.indexer_v2 import KnowledgeIndexer
from lina.rag.retriever import Retriever

# Индексация
indexer = KnowledgeIndexer()
indexer.index_all()

# Поиск
retriever = Retriever()
results = retriever.search("pacman", top_k=5)
```

## System

```python
from lina.system.diagnostics import SystemDiagnostics
from lina.system.package_manager import PackageManager

diag = SystemDiagnostics()
info = diag.collect_all()

pm = PackageManager()
pm.search("firefox")
```

## Diagnostics

```python
from lina.diagnostics.engine import DiagnosticEngine

engine = DiagnosticEngine()
engine.load_trees()
report = engine.diagnose("wifi")
```

## GUI

```python
from lina.gui import is_gui_available
from lina.gui.chat import ChatController

if is_gui_available():
    chat = ChatController()
    chat.send_user_message("Привет!")
```

## Voice

```python
from lina.voice.stt import SpeechToText
from lina.voice.tts import TextToSpeech
from lina.voice.pipeline import create_voice_pipeline

stt = SpeechToText()
tts = TextToSpeech()
pipeline = create_voice_pipeline()
```

## I18n

```python
from lina.core.i18n import get_i18n

i18n = get_i18n("ru")
print(i18n.t("chat.welcome"))
# → "Привет! Я Lina, ваш Linux-помощник. Чем могу помочь?"
```

## Prompts

```python
from lina.core.prompts import PromptBuilder, SystemContext

builder = PromptBuilder(system_ctx=SystemContext(
    distro="CachyOS",
    package_manager="pacman"
))
system_prompt = builder.get_system_prompt()
rag_prompt = builder.build_rag_prompt("вопрос", "контекст")
```

## Metrics

```python
from lina.core.metrics import get_metrics, ResponseSource

metrics = get_metrics()
metrics.start_query("Как обновить?")
# ... обработка ...
metrics.end_query(source=ResponseSource.RAG)

summary = metrics.get_summary()
```

## Installer

```python
from lina.installer.first_run import FirstRunWizard
from lina.installer.updater import LinaUpdater

# Первый запуск
wizard = FirstRunWizard()
if wizard.is_first_run():
    wizard.select_model("medium")
    wizard.simulate_download()

# Обновления
updater = LinaUpdater()
updater.check_for_updates()
```
