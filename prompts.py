"""Every string the dialog LLM or the plugin panel can be shown.

Same reasoning as the sibling ``mcbot_bridge`` / ``game_agent_minecraft``
plugins: without a dedicated table the plugin would emit unconditional Chinese,
and a non-Chinese user would get Chinese narration cutting into their own
conversation. Pulling every string out makes a missing translation impossible
to overlook.

Two families live here:

* **Tool-result lines** — returned by ``@llm_tool`` / ``@plugin_entry``. These
  land in the conversation transcript permanently, so the panel's own error
  prose, its host name, its port and its endpoint paths never appear in them.
  Only categories and data (a window size, a task name) do.
* **Cue bodies** — pushed through ``push_message``. These may carry
  ``{{MASTER_NAME}}`` / ``{{LANLAN_NAME}}``, which main_server expands per
  session at the injection boundary; the plugin cannot know the right form
  itself and must not hardcode "用户" / "master".

Locale keys follow ``config/prompts``: ``zh`` means Simplified (not
``zh-CN``) and ``zh-TW`` is separate. ``en`` is the documented fallback.
"""
from __future__ import annotations

from typing import Any, Dict

DEFAULT_LANG = "en"
SUPPORTED_LANGS: tuple[str, ...] = ("zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")

PROMPTS: Dict[str, Dict[str, str]] = {
    # ------------------------------------------------------------------
    # Transport / precondition failures (tool results)
    # ------------------------------------------------------------------
    "ERR_UNREACHABLE": {
        "zh": "现在连不上终末地执行器——控制面板后端没在运行，或者刚断开。要让{MASTER_NAME}先双击 .workbuddy\\start_panel.bat 把面板起起来。",
        "zh-TW": "現在連不上終末地執行器——控制面板後端沒在執行，或者剛斷開。要請{MASTER_NAME}先雙擊 .workbuddy\\start_panel.bat 把面板啟動起來。",
        "en": "I can't reach the Endfield executor right now — its control panel backend is not running, or it just dropped. Please start the panel first (double-click .workbuddy\\start_panel.bat).",
        "ja": "今、エンドフィールドの実行器につながりません——コントロールパネルのバックエンドが起動していないか、切れたところです。先にパネルを起動してください（.workbuddy\\start_panel.bat をダブルクリック）。",
        "ko": "지금 엔드필드 실행기에 연결할 수 없어요 — 제어 패널 백엔드가 실행 중이 아니거나 방금 끊겼어요. 먼저 패널을 실행해 주세요(.workbuddy\\start_panel.bat 더블클릭).",
        "ru": "Сейчас не могу связаться с исполнителем Endfield — серверная часть панели управления не запущена или только что отключилась. Сначала запустите панель (.workbuddy\\start_panel.bat).",
        "es": "Ahora mismo no puedo contactar con el ejecutor de Endfield: el servidor del panel de control no está en marcha o se acaba de desconectar. Inicia primero el panel (.workbuddy\\start_panel.bat).",
        "pt": "Agora não consigo falar com o executor de Endfield — o backend do painel de controle não está em execução ou acabou de cair. Inicie o painel primeiro (.workbuddy\\start_panel.bat).",
    },
    "ERR_NO_TOKEN": {
        "zh": "控制面板在运行，但读不到它的令牌文件 panel_token.txt。面板可能刚启动还没写完，或者 panel_dir 配置指错了目录。",
        "zh-TW": "控制面板在執行，但讀不到它的權杖檔 panel_token.txt。面板可能剛啟動還沒寫完，或者 panel_dir 設定指錯目錄了。",
        "en": "The control panel is up, but its token file panel_token.txt could not be read. It may have just started, or the panel_dir setting points at the wrong folder.",
        "ja": "コントロールパネルは動いていますが、トークンファイル panel_token.txt を読めません。起動直後か、panel_dir の設定が別のフォルダを指しています。",
        "ko": "제어 패널은 실행 중인데 토큰 파일 panel_token.txt를 읽을 수 없어요. 방금 시작했거나 panel_dir 설정이 다른 폴더를 가리키고 있어요.",
        "ru": "Панель управления запущена, но файл токена panel_token.txt не читается. Возможно, она только что стартовала или panel_dir указывает не на ту папку.",
        "es": "El panel de control está activo, pero no se puede leer su archivo de token panel_token.txt. Puede que acabe de arrancar o que panel_dir apunte a otra carpeta.",
        "pt": "O painel de controle está ativo, mas não consigo ler o arquivo de token panel_token.txt. Talvez ele tenha acabado de iniciar, ou o panel_dir aponta para outra pasta.",
    },
    "ERR_AUTH": {
        "zh": "执行器拒绝了我们的令牌——面板刚重启过，换了新令牌，请再试一次。",
        "zh-TW": "執行器拒絕了我們的權杖——面板剛重啟過，換了新權杖，請再試一次。",
        "en": "The executor rejected our token — the panel restarted and rotated it. Please try again.",
        "ja": "実行器がトークンを拒否しました——パネルが再起動してトークンが変わったようです。もう一度試してください。",
        "ko": "실행기가 토큰을 거부했어요 — 패널이 재시작하면서 토큰이 바뀌었어요. 다시 시도해 주세요.",
        "ru": "Исполнитель отклонил наш токен — панель перезапустилась и сменила его. Попробуйте ещё раз.",
        "es": "El ejecutor rechazó nuestro token: el panel se reinició y lo cambió. Inténtalo de nuevo.",
        "pt": "O executor recusou nosso token — o painel reiniciou e trocou. Tente de novo.",
    },
    "ERR_FORBIDDEN": {
        "zh": "执行器拒绝了这次请求的来源（Host/Origin 白名单）。base_url 必须是 127.0.0.1 或 localhost。",
        "zh-TW": "執行器拒絕了這次請求的來源（Host/Origin 白名單）。base_url 必須是 127.0.0.1 或 localhost。",
        "en": "The executor rejected where this request came from (its Host/Origin allow-list). base_url has to be 127.0.0.1 or localhost.",
        "ja": "実行器がリクエストの送信元を拒否しました（Host/Origin の許可リスト）。base_url は 127.0.0.1 か localhost である必要があります。",
        "ko": "실행기가 요청 출처를 거부했어요(Host/Origin 허용 목록). base_url은 127.0.0.1 또는 localhost여야 해요.",
        "ru": "Исполнитель отклонил источник запроса (список Host/Origin). base_url должен быть 127.0.0.1 или localhost.",
        "es": "El ejecutor rechazó el origen de esta petición (lista de Host/Origin). base_url tiene que ser 127.0.0.1 o localhost.",
        "pt": "O executor recusou a origem desta requisição (lista de Host/Origin). base_url precisa ser 127.0.0.1 ou localhost.",
    },
    "ERR_NOT_READY": {
        "zh": "执行器还在初始化，等它准备好再试。",
        "zh-TW": "執行器還在初始化，等它準備好再試。",
        "en": "The executor is still initialising — try again once it is ready.",
        "ja": "実行器はまだ初期化中です。準備ができてからもう一度。",
        "ko": "실행기가 아직 초기화 중이에요. 준비되면 다시 시도해 주세요.",
        "ru": "Исполнитель ещё инициализируется — попробуйте, когда он будет готов.",
        "es": "El ejecutor sigue inicializándose; prueba cuando esté listo.",
        "pt": "O executor ainda está inicializando — tente quando estiver pronto.",
    },
    "ERR_GENERIC": {
        "zh": "执行器那边报了个错，具体原因我记在插件日志里了。",
        "zh-TW": "執行器那邊報了個錯，具體原因我記在外掛日誌裡了。",
        "en": "The executor reported an error; the details are in the plugin log.",
        "ja": "実行器がエラーを返しました。詳しい内容はプラグインのログにあります。",
        "ko": "실행기가 오류를 보고했어요. 자세한 내용은 플러그인 로그에 있어요.",
        "ru": "Исполнитель вернул ошибку; подробности — в журнале плагина.",
        "es": "El ejecutor devolvió un error; los detalles están en el registro del plugin.",
        "pt": "O executor retornou um erro; os detalhes estão no log do plugin.",
    },
    "ERR_NOT_ADMIN": {
        "zh": "执行器不是以管理员身份运行的——发给游戏的键鼠操作不会生效，任务必然失败。请用 start_panel.bat（会自动提权）重启面板。",
        "zh-TW": "執行器不是以系統管理員身分執行的——送給遊戲的鍵鼠操作不會生效，任務必定失敗。請用 start_panel.bat（會自動提權）重啟面板。",
        "en": "The executor is not running as administrator, so the keystrokes it sends to the game go nowhere and the task is guaranteed to fail. Restart the panel with start_panel.bat (it requests elevation).",
        "ja": "実行器が管理者権限で動いていません。ゲームに送るキー操作が届かず、タスクは必ず失敗します。start_panel.bat（自動で昇格します）でパネルを再起動してください。",
        "ko": "실행기가 관리자 권한으로 실행되지 않아서 게임으로 보내는 입력이 전달되지 않고 작업이 반드시 실패해요. start_panel.bat(자동 승격)으로 패널을 다시 시작해 주세요.",
        "ru": "Исполнитель запущен не от администратора: ввод, который он посылает игре, не доходит, и задача гарантированно провалится. Перезапустите панель через start_panel.bat (он запросит повышение прав).",
        "es": "El ejecutor no se ejecuta como administrador, así que las pulsaciones que envía al juego no llegan y la tarea fallará seguro. Reinicia el panel con start_panel.bat (pide elevación).",
        "pt": "O executor não está rodando como administrador, então as teclas que ele envia ao jogo não chegam e a tarefa vai falhar com certeza. Reinicie o painel com start_panel.bat (ele pede elevação).",
    },
    "ERR_RESOLUTION": {
        "zh": "游戏窗口现在是 {w}x{h}（比例 {ratio}），不是 16:9。执行器的 go-service 会直接停掉任务，所以先把游戏分辨率改成 16:9 再跑。",
        "zh-TW": "遊戲視窗現在是 {w}x{h}（比例 {ratio}），不是 16:9。執行器的 go-service 會直接停掉任務，所以先把遊戲解析度改成 16:9 再跑。",
        "en": "The game window is {w}x{h} (ratio {ratio}), not 16:9. The executor's go-service stops the task outright at any other ratio, so set the game to 16:9 first.",
        "ja": "ゲームウィンドウが {w}x{h}（比率 {ratio}）で 16:9 ではありません。実行器の go-service は他の比率だとタスクを即停止するので、先に 16:9 にしてください。",
        "ko": "게임 창이 {w}x{h}(비율 {ratio})로 16:9가 아니에요. 실행기의 go-service는 다른 비율이면 작업을 바로 중단하니, 먼저 16:9로 맞춰 주세요.",
        "ru": "Окно игры сейчас {w}x{h} (соотношение {ratio}), а не 16:9. go-service исполнителя в таком случае сразу останавливает задачу — сначала поставьте 16:9.",
        "es": "La ventana del juego es {w}x{h} (proporción {ratio}), no 16:9. El go-service del ejecutor detiene la tarea de inmediato con otra proporción, así que pon el juego en 16:9 primero.",
        "pt": "A janela do jogo está {w}x{h} (proporção {ratio}), não 16:9. O go-service do executor para a tarefa na hora com outra proporção, então coloque o jogo em 16:9 primeiro.",
    },
    "WARN_NOT_FOCUSED": {
        "zh": "（另外：游戏窗口现在不在前台。执行器会在开始前抢一次焦点，抢不回来任务就会失败。）",
        "zh-TW": "（另外：遊戲視窗現在不在前景。執行器會在開始前搶一次焦點，搶不回來任務就會失敗。）",
        "en": "(Also: the game window is not in the foreground. The executor grabs focus before starting, but if that fails the task will not progress.)",
        "ja": "（なお、ゲームウィンドウが前面にありません。実行器は開始前にフォーカスを取り直しますが、取れないとタスクは進みません。）",
        "ko": "(참고: 게임 창이 전면에 없어요. 실행기가 시작 전에 포커스를 되찾지만, 실패하면 작업이 진행되지 않아요.)",
        "ru": "(Ещё: окно игры не на переднем плане. Исполнитель попробует перехватить фокус перед запуском, но если не выйдет, задача не пойдёт.)",
        "es": "(Además: la ventana del juego no está en primer plano. El ejecutor intentará recuperar el foco antes de empezar; si no lo consigue, la tarea no avanzará).",
        "pt": "(Também: a janela do jogo não está em primeiro plano. O executor tenta retomar o foco antes de começar; se não conseguir, a tarefa não avança.)",
    },
    "ERR_BUSY": {
        "zh": "执行器已经有任务在跑了：{current}。要换任务就先停下来。",
        "zh-TW": "執行器已經有任務在跑了：{current}。要換任務就先停下來。",
        "en": "The executor is already running a task: {current}. Stop it first if you want a different one.",
        "ja": "実行器はすでにタスクを実行中です：{current}。別のタスクにするなら先に止めてください。",
        "ko": "실행기가 이미 작업을 실행 중이에요: {current}. 다른 작업을 하려면 먼저 멈춰 주세요.",
        "ru": "Исполнитель уже выполняет задачу: {current}. Сначала остановите её, если нужна другая.",
        "es": "El ejecutor ya está ejecutando una tarea: {current}. Deténla primero si quieres otra.",
        "pt": "O executor já está rodando uma tarefa: {current}. Pare-a antes se quiser outra.",
    },
    "ERR_UNKNOWN_TASK": {
        "zh": "执行器里没有叫「{query}」的任务。{candidates}",
        "zh-TW": "執行器裡沒有叫「{query}」的任務。{candidates}",
        "en": "There is no task called \"{query}\" in the executor. {candidates}",
        "ja": "実行器に「{query}」というタスクはありません。{candidates}",
        "ko": "실행기에 \"{query}\"라는 작업이 없어요. {candidates}",
        "ru": "В исполнителе нет задачи «{query}». {candidates}",
        "es": "No hay ninguna tarea llamada \"{query}\" en el ejecutor. {candidates}",
        "pt": "Não existe tarefa chamada \"{query}\" no executor. {candidates}",
    },
    "ERR_AMBIGUOUS": {
        "zh": "「{query}」可能指这几个任务，挑一个告诉我：{candidates}",
        "zh-TW": "「{query}」可能指這幾個任務，挑一個告訴我：{candidates}",
        "en": "\"{query}\" could mean a few of these — tell me which one: {candidates}",
        "ja": "「{query}」はこのあたりのどれかかも。どれか教えて：{candidates}",
        "ko": "\"{query}\"는 이 중 몇 개일 수 있어요 — 어느 건지 알려 주세요: {candidates}",
        "ru": "«{query}» может означать несколько из них — уточните, какая: {candidates}",
        "es": "\"{query}\" puede ser varias de estas; dime cuál: {candidates}",
        "pt": "\"{query}\" pode ser várias destas — me diga qual: {candidates}",
    },
    "CATALOGUE": {
        "zh": "执行器现在能跑这些任务（括号里是代号，可以直接用它）：{catalogue}",
        "zh-TW": "執行器現在能跑這些任務（括號裡是代號，可以直接用它）：{catalogue}",
        "en": "The executor can run these tasks right now (the code in brackets works directly): {catalogue}",
        "ja": "実行器が今実行できるタスクはこちらです（括弧内のコードをそのまま使えます）：{catalogue}",
        "ko": "실행기가 지금 실행할 수 있는 작업들이에요 (괄호 안 코드를 그대로 써도 돼요): {catalogue}",
        "ru": "Сейчас исполнитель умеет запускать эти задачи (код в скобках можно называть прямо): {catalogue}",
        "es": "Ahora mismo el ejecutor puede hacer estas tareas (el código entre corchetes sirve tal cual): {catalogue}",
        "pt": "Agora o executor consegue rodar estas tarefas (o código entre colchetes serve direto): {catalogue}",
    },
    "CATALOGUE_EMPTY": {
        "zh": "执行器还没准备好，拿不到任务列表。等它起来再问，或者直接说个大概的任务名，我来对。",
        "zh-TW": "執行器還沒準備好，拿不到任務列表。等它起來再問，或者直接說個大概的任務名，我來對。",
        "en": "The executor is not ready yet, so I cannot fetch the task list. Try again once it is up, or just name the task roughly and I will match it.",
        "ja": "実行器がまだ準備できていないので、タスク一覧が取れません。起動してから聞くか、ざっくりした名前で言ってもらえればこちらで照合します。",
        "ko": "실행기가 아직 준비되지 않아서 작업 목록을 가져올 수 없어요. 실행된 뒤에 물어보시거나, 대략적인 이름을 말해 주시면 제가 맞춰 볼게요.",
        "ru": "Исполнитель ещё не готов, список задач недоступен. Спросите, когда он поднимется, или назовите задачу примерно — я сопоставлю.",
        "es": "El ejecutor aún no está listo, así que no puedo obtener la lista de tareas. Pregunta cuando arranque, o di el nombre a grandes rasgos y lo emparejo yo.",
        "pt": "O executor ainda não está pronto, então não consigo buscar a lista de tarefas. Pergunte quando ele subir, ou diga o nome por alto que eu encaixo.",
    },
    "ACK_DISPATCHED": {
        "zh": "已经让执行器开始跑「{label}」了，最多跑 {minutes} 分钟。它跑完会自己来报，先别急着追问。",
        "zh-TW": "已經讓執行器開始跑「{label}」了，最多跑 {minutes} 分鐘。它跑完會自己來報，先別急著追問。",
        "en": "The executor has started \"{label}\" — up to {minutes} minutes. It will report back on its own, so no need to keep asking.",
        "ja": "実行器に「{label}」を開始させました（最大 {minutes} 分）。終わったら向こうから報告が来るので、急いで聞き返さなくて大丈夫です。",
        "ko": "실행기에 \"{label}\"를 시작시켰어요 — 최대 {minutes}분이에요. 끝나면 알아서 보고하니 계속 물어보지 않아도 돼요.",
        "ru": "Исполнитель начал «{label}» — до {minutes} минут. Он сам сообщит о завершении, так что переспрашивать не нужно.",
        "es": "El ejecutor ha empezado \"{label}\": hasta {minutes} minutos. Avisará por su cuenta cuando acabe, no hace falta preguntar.",
        "pt": "O executor começou \"{label}\" — até {minutes} minutos. Ele avisa sozinho quando terminar, não precisa ficar perguntando.",
    },
    "ACK_DISPATCHED_NO_TIMEOUT": {
        "zh": "已经让执行器开始跑「{label}」了，用执行器自己的时间上限。它跑完会自己来报，先别急着追问。",
        "zh-TW": "已經讓執行器開始跑「{label}」了，用執行器自己的時間上限。它跑完會自己來報，先別急著追問。",
        "en": "The executor has started \"{label}\" with its own time limit. It will report back on its own, so no need to keep asking.",
        "ja": "実行器に「{label}」を開始させました（実行器側の時間上限を使用）。終わったら向こうから報告が来ます。",
        "ko": "실행기에 \"{label}\"를 시작시켰어요(실행기 자체 시간 제한 사용). 끝나면 알아서 보고해요.",
        "ru": "Исполнитель начал «{label}» со своим собственным лимитом времени. О завершении сообщит сам.",
        "es": "El ejecutor ha empezado \"{label}\" con su propio límite de tiempo. Avisará por su cuenta al terminar.",
        "pt": "O executor começou \"{label}\" com o próprio limite de tempo. Ele avisa sozinho quando acabar.",
    },
    "ERR_PREPARE": {
        "zh": "执行器初始化失败了——多半是终末地没开，或者游戏窗口还没出来。等进到游戏里再试一次。",
        "zh-TW": "執行器初始化失敗了——多半是終末地沒開，或者遊戲視窗還沒出來。等進到遊戲裡再試一次。",
        "en": "The executor failed to initialise — usually that means Endfield is not running, or its window has not appeared yet. Try again once you are in game.",
        "ja": "実行器の初期化に失敗しました——たいていはエンドフィールドが起動していないか、ウィンドウがまだ出ていないだけです。ゲームに入ってから再試行してください。",
        "ko": "실행기 초기화에 실패했어요 — 보통 엔드필드가 실행 중이 아니거나 창이 아직 안 뜬 경우예요. 게임에 들어간 뒤 다시 시도해 주세요.",
        "ru": "Исполнитель не смог инициализироваться — обычно это значит, что Endfield не запущен или его окно ещё не появилось. Попробуйте, когда войдёте в игру.",
        "es": "El ejecutor no pudo inicializarse: normalmente significa que Endfield no está en marcha o que su ventana aún no ha aparecido. Prueba cuando estés dentro del juego.",
        "pt": "O executor não conseguiu inicializar — normalmente significa que Endfield não está rodando ou que a janela ainda não apareceu. Tente quando estiver no jogo.",
    },
    "ERR_TASK_SCHEMA": {
        "zh": "没听清要跑哪个任务——把任务名或者大概的说法再说一次，我来对。",
        "zh-TW": "沒聽清要跑哪個任務——把任務名或者大概的說法再說一次，我來對。",
        "en": "I did not catch which task to run — say the task name, or roughly what it should do, and I will match it.",
        "ja": "どのタスクを走らせるのか聞き取れませんでした。タスク名か、だいたいの内容をもう一度言ってください。",
        "ko": "어떤 작업을 돌릴지 못 알아들었어요 — 작업 이름이나 대략적인 설명을 다시 말해 주세요.",
        "ru": "Не разобрал, какую задачу запускать — назовите её или опишите примерно, я сопоставлю.",
        "es": "No he entendido qué tarea ejecutar; di el nombre o describe más o menos qué hace y la emparejo.",
        "pt": "Não entendi qual tarefa rodar — diga o nome ou descreva mais ou menos o que é, que eu encaixo.",
    },
    "ERR_STOP_NOTHING": {
        "zh": "执行器现在没有在跑的任务，不用停。",
        "zh-TW": "執行器現在沒有在跑的任務，不用停。",
        "en": "The executor has nothing running, so there is nothing to stop.",
        "ja": "実行器は何も動いていないので、止めるものはありません。",
        "ko": "실행기가 지금 돌리는 작업이 없어서 멈출 게 없어요.",
        "ru": "У исполнителя сейчас ничего не выполняется — останавливать нечего.",
        "es": "El ejecutor no tiene nada en marcha, así que no hay nada que detener.",
        "pt": "O executor não tem nada rodando, então não há o que parar.",
    },
    "ACK_STOPPED": {
        "zh": "已经让执行器停下「{label}」了。",
        "zh-TW": "已經讓執行器停下「{label}」了。",
        "en": "I told the executor to stop \"{label}\".",
        "ja": "実行器に「{label}」を止めるよう伝えました。",
        "ko": "실행기에 \"{label}\"를 멈추라고 했어요.",
        "ru": "Я попросил исполнителя остановить «{label}».",
        "es": "Le he dicho al ejecutor que detenga \"{label}\".",
        "pt": "Eu pedi ao executor para parar \"{label}\".",
    },
    "ACK_STOP_FAILED": {
        "zh": "执行器没有确认这次停止——它可能已经在收尾了，再等一会儿看看。",
        "zh-TW": "執行器沒有確認這次停止——它可能已經在收尾了，再等一下看看。",
        "en": "The executor did not confirm the stop — it may already be wrapping up. Give it a moment.",
        "ja": "実行器が停止を確認しませんでした——すでに片付け中かもしれません。少し待ってみてください。",
        "ko": "실행기가 중지를 확인하지 않았어요 — 이미 마무리 중일 수 있어요. 잠시 기다려 보세요.",
        "ru": "Исполнитель не подтвердил остановку — возможно, он уже завершает работу. Подождите немного.",
        "es": "El ejecutor no confirmó la parada; puede que ya esté terminando. Dale un momento.",
        "pt": "O executor não confirmou a parada — talvez já esteja finalizando. Espere um pouco.",
    },
    # ------------------------------------------------------------------
    # Cues (conversation)
    # ------------------------------------------------------------------
    "CUE_DONE_OK": {
        "zh": "终末地那边「{label}」跑完了，用时 {elapsed}。",
        "zh-TW": "終末地那邊「{label}」跑完了，用時 {elapsed}。",
        "en": "The Endfield run of \"{label}\" finished in {elapsed}.",
        "ja": "エンドフィールドの「{label}」が {elapsed} で完了しました。",
        "ko": "엔드필드 \"{label}\" 작업이 {elapsed} 만에 끝났어요.",
        "ru": "Задача «{label}» в Endfield завершилась за {elapsed}.",
        "es": "La tarea \"{label}\" en Endfield terminó en {elapsed}.",
        "pt": "A tarefa \"{label}\" no Endfield terminou em {elapsed}.",
    },
    "CUE_DONE_FAILED": {
        "zh": "终末地那边「{label}」没跑成功（用时 {elapsed}），执行器判定为失败。",
        "zh-TW": "終末地那邊「{label}」沒跑成功（用時 {elapsed}），執行器判定為失敗。",
        "en": "The Endfield run of \"{label}\" failed after {elapsed}.",
        "ja": "エンドフィールドの「{label}」は {elapsed} で失敗しました。",
        "ko": "엔드필드 \"{label}\" 작업이 {elapsed} 만에 실패했어요.",
        "ru": "Задача «{label}» в Endfield завершилась неудачей за {elapsed}.",
        "es": "La tarea \"{label}\" en Endfield falló tras {elapsed}.",
        "pt": "A tarefa \"{label}\" no Endfield falhou depois de {elapsed}.",
    },
    "CUE_DONE_INCOMPLETE": {
        "zh": "终末地那边「{label}」没跑完就停了（用时 {elapsed}）——可能是超时，也可能是被中途停下。",
        "zh-TW": "終末地那邊「{label}」沒跑完就停了（用時 {elapsed}）——可能是逾時，也可能是被中途停下。",
        "en": "The Endfield run of \"{label}\" stopped before finishing, after {elapsed} — it may have timed out or been cut short.",
        "ja": "エンドフィールドの「{label}」は {elapsed} で途中停止しました——タイムアウトか、途中で止められた可能性があります。",
        "ko": "엔드필드 \"{label}\" 작업이 {elapsed} 만에 중간에 멈췄어요 — 시간 초과이거나 중단됐을 수 있어요.",
        "ru": "Задача «{label}» в Endfield остановилась, не дойдя до конца, через {elapsed} — возможно, тайм-аут или её прервали.",
        "es": "La tarea \"{label}\" en Endfield se detuvo antes de acabar, tras {elapsed}: puede haber caducado o haber sido interrumpida.",
        "pt": "A tarefa \"{label}\" no Endfield parou antes de terminar, após {elapsed} — pode ter estourado o tempo ou sido interrompida.",
    },
    "CUE_PROGRESS": {
        "zh": "终末地还在跑「{label}」，现在到 {nodes}，已经 {elapsed}。",
        "zh-TW": "終末地還在跑「{label}」，現在到 {nodes}，已經 {elapsed}。",
        "en": "The Endfield run of \"{label}\" is still going — now at {nodes}, {elapsed} in.",
        "ja": "エンドフィールドの「{label}」は進行中——今は {nodes}、{elapsed} 経過。",
        "ko": "엔드필드 \"{label}\" 작업이 아직 진행 중이에요 — 지금 {nodes}, {elapsed} 경과.",
        "ru": "Задача «{label}» в Endfield ещё идёт — сейчас на {nodes}, прошло {elapsed}.",
        "es": "La tarea \"{label}\" en Endfield sigue en marcha: ahora en {nodes}, {elapsed} transcurridos.",
        "pt": "A tarefa \"{label}\" no Endfield ainda está rodando — agora em {nodes}, {elapsed} decorridos.",
    },
    "CUE_LOST": {
        "zh": "和终末地执行器的联系断了，「{label}」现在是什么状态我不知道——别去重跑它，等执行器回来再说。",
        "zh-TW": "和終末地執行器的聯繫斷了，「{label}」現在是什麼狀態我不知道——別去重跑它，等執行器回來再說。",
        "en": "I lost contact with the Endfield executor, so I do not know what state \"{label}\" is in now — do not re-run it; wait for the executor to come back.",
        "ja": "エンドフィールドの実行器との接続が切れました。「{label}」が今どうなっているかは分かりません——再実行せず、実行器が戻るのを待ってください。",
        "ko": "엔드필드 실행기와 연결이 끊겼어요. \"{label}\"가 지금 어떤 상태인지 몰라요 — 다시 실행하지 말고 실행기가 돌아오길 기다려 주세요.",
        "ru": "Связь с исполнителем Endfield потеряна, поэтому я не знаю, в каком состоянии сейчас «{label}» — не перезапускайте её, дождитесь исполнителя.",
        "es": "He perdido el contacto con el ejecutor de Endfield, así que no sé en qué estado está ahora \"{label}\": no la relances, espera a que vuelva el ejecutor.",
        "pt": "Perdi o contato com o executor do Endfield, então não sei em que estado \"{label}\" está agora — não rode de novo, espere o executor voltar.",
    },
    "CUE_TIMEOUT": {
        "zh": "等「{label}」等太久了，我不再盯着它了——它可能还在跑。",
        "zh-TW": "等「{label}」等太久了，我不再盯著它了——它可能還在跑。",
        "en": "I waited too long for \"{label}\" and stopped watching — it may still be running.",
        "ja": "「{label}」を待ちすぎたので、もう見張るのをやめました——まだ動いているかもしれません。",
        "ko": "\"{label}\"를 너무 오래 기다려서 그만 지켜보기로 했어요 — 아직 돌아가고 있을 수도 있어요.",
        "ru": "Я слишком долго ждал «{label}» и перестал следить — возможно, она всё ещё идёт.",
        "es": "Esperé demasiado a \"{label}\" y dejé de vigilarla; puede que siga en marcha.",
        "pt": "Esperei demais por \"{label}\" e parei de acompanhar — ela pode ainda estar rodando.",
    },
    "INTERNAL_GAG": {
        "zh": "（这些是执行器的内部状态，别照念，用你自己的话说。）",
        "zh-TW": "（這些是執行器的內部狀態，別照唸，用你自己的話說。）",
        "en": "(These are the executor's internal state names — do not read them out; say it in your own words.)",
        "ja": "（これは実行器の内部状態名です。そのまま読み上げず、自分の言葉で。）",
        "ko": "(이건 실행기 내부 상태 이름이에요. 그대로 읽지 말고 자기 말로 하세요.)",
        "ru": "(Это внутренние имена состояний исполнителя — не зачитывайте их, скажите своими словами.)",
        "es": "(Estos son nombres internos del ejecutor: no los leas tal cual, dilo con tus palabras).",
        "pt": "(Estes são nomes internos do executor — não leia em voz alta; diga com suas palavras.)",
    },
    # ------------------------------------------------------------------
    # Status surface (panel entry)
    # ------------------------------------------------------------------
    "STATUS_NOT_CONNECTED": {
        "zh": "执行器没在运行",
        "zh-TW": "執行器沒在執行",
        "en": "executor not running",
        "ja": "実行器は停止中",
        "ko": "실행기 미실행",
        "ru": "исполнитель не запущен",
        "es": "ejecutor no activo",
        "pt": "executor inativo",
    },
    "STATUS_READY": {
        "zh": "就绪，没在跑任务",
        "zh-TW": "就緒，沒在跑任務",
        "en": "ready, idle",
        "ja": "準備完了・待機中",
        "ko": "준비됨, 대기 중",
        "ru": "готов, простаивает",
        "es": "listo, en reposo",
        "pt": "pronto, ocioso",
    },
    "STATUS_BUSY": {
        "zh": "正在跑「{task}」",
        "zh-TW": "正在跑「{task}」",
        "en": "running \"{task}\"",
        "ja": "「{task}」を実行中",
        "ko": "\"{task}\" 실행 중",
        "ru": "выполняет «{task}»",
        "es": "ejecutando \"{task}\"",
        "pt": "executando \"{task}\"",
    },
    "STATUS_PREPARING": {
        "zh": "已连上，还没初始化",
        "zh-TW": "已連上，還沒初始化",
        "en": "reachable, not initialised",
        "ja": "接続済み・未初期化",
        "ko": "연결됨, 초기화 전",
        "ru": "на связи, не инициализирован",
        "es": "accesible, sin inicializar",
        "pt": "acessível, não inicializado",
    },
    "STATUS_SUMMARY": {
        "zh": "终末地执行器：{state_label}。",
        "zh-TW": "終末地執行器：{state_label}。",
        "en": "Endfield executor: {state_label}.",
        "ja": "エンドフィールド実行器：{state_label}。",
        "ko": "엔드필드 실행기: {state_label}.",
        "ru": "Исполнитель Endfield: {state_label}.",
        "es": "Ejecutor de Endfield: {state_label}.",
        "pt": "Executor do Endfield: {state_label}.",
    },
    "STATUS_NOT_ADMIN_SUFFIX": {
        "zh": "（它没有管理员权限，喂给游戏的按键不会生效。）",
        "zh-TW": "（它沒有系統管理員權限，餵給遊戲的按鍵不會生效。）",
        "en": " (It is not elevated, so input sent to the game will not take effect.)",
        "ja": "（管理者権限がないため、ゲームへの入力は反映されません。）",
        "ko": " (관리자 권한이 없어서 게임 입력이 반영되지 않아요.)",
        "ru": " (Он без прав администратора — ввод в игру не сработает.)",
        "es": " (No tiene privilegios, así que la entrada al juego no surtirá efecto).",
        "pt": " (Ele não está elevado, então a entrada no jogo não terá efeito.)",
    },
    "LABEL_IDLE_TASK": {
        "zh": "空闲",
        "zh-TW": "空閒",
        "en": "idle",
        "ja": "待機中",
        "ko": "대기",
        "ru": "простой",
        "es": "en reposo",
        "pt": "ocioso",
    },
}


def t(key: str, *, lang: str | None = None, **fmt: Any) -> str:
    """Resolve a prompt by key + locale, with optional ``str.format`` values.

    Falls back to English when the locale is missing — except for Chinese
    variants, which fall back to Simplified first: a ``zh-TW`` bundle someone
    forgot to fill in should degrade to Simplified (still readable) rather than
    to English (a mid-conversation language switch). Raises ``KeyError`` on an
    unknown key so a typo fails loudly instead of rendering an empty line.
    """
    bundle = PROMPTS[key]
    lang_key = lang or DEFAULT_LANG
    text = bundle.get(lang_key)
    if not text and lang_key.startswith("zh"):
        text = bundle.get("zh")
    text = text or bundle[DEFAULT_LANG]
    if fmt:
        text = text.format(**fmt)
    # ``{{MASTER_NAME}}`` only collapses to ``{MASTER_NAME}`` when
    # ``str.format`` actually runs. Keys that take no values skip the branch
    # above and would keep the doubled braces, which main_server does not
    # recognise — collapse unconditionally so both paths agree.
    return text.replace("{{MASTER_NAME}}", "{MASTER_NAME}").replace(
        "{{LANLAN_NAME}}", "{LANLAN_NAME}"
    )


def user_lang() -> str:
    """Best-effort current user language, as a key of the tables above.

    Reads ``utils.language_utils.get_global_language_full`` (initialised from
    Steam settings, then the system locale) and normalises it with
    ``prompts_sys.normalize_sys_prompt_locale`` — the canonical normaliser for
    this key scheme. The raw value must NOT be used directly: it renders
    Simplified as ``zh-CN``, which is not a key here, and the guard below would
    then drop every Simplified Chinese user to English.

    Returns ``DEFAULT_LANG`` on any failure so prompt resolution never raises
    on the read side.
    """
    try:
        from config.prompts.prompts_sys import normalize_sys_prompt_locale
        from utils.language_utils import get_global_language_full

        raw = get_global_language_full() or ""
        code = normalize_sys_prompt_locale(raw) or ""
        code = code.strip()
        if code in SUPPORTED_LANGS:
            return code
        if code.lower().startswith("zh"):
            return "zh"
    except Exception:
        pass
    return DEFAULT_LANG


def gag(lang: str | None = None) -> str:
    """The "do not read this out" aside appended to narration cues."""
    return t("INTERNAL_GAG", lang=lang)


def format_elapsed(seconds: Any, *, lang: str | None = None) -> str:
    """Render an elapsed duration the way the surrounding sentence expects.

    The panel reports whole seconds; anything past a minute is far easier to
    say as ``3 分 20 秒``. Kept locale-agnostic on purpose — the units are the
    only part that would differ, and digits plus a two-character unit read
    correctly in all eight locales here.
    """
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return "-"
    if total < 0:
        total = 0
    if total < 60:
        return f"{total}s"
    minutes, rest = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m{rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def format_catalogue(tasks: Any, *, limit: int = 60) -> str:
    """One line of ``label (name)`` pairs, grouped label first.

    Deliberately terse: this string goes into a tool result, i.e. into the
    conversation transcript. Group names come from the resource's own
    ``group`` field so the model can hear "日常" / "other_menu" and pick.
    """
    if not isinstance(tasks, list) or not tasks:
        return ""
    parts: list[str] = []
    for item in tasks[:limit]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        label = str(item.get("label") or "").strip() or name
        if name and label and label != name:
            parts.append(f"{label} ({name})")
        elif name:
            parts.append(name)
    return "; ".join(parts)


def format_candidates(tasks: Any, *, limit: int = 8) -> str:
    """The "did you mean" list used by the unknown/ambiguous branches."""
    return format_catalogue(tasks, limit=limit)


__all__ = [
    "DEFAULT_LANG",
    "SUPPORTED_LANGS",
    "PROMPTS",
    "t",
    "user_lang",
    "gag",
    "format_elapsed",
    "format_catalogue",
    "format_candidates",
]
