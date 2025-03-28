--- Cleaning up potential old session files ---
--- Session cleanup finished ---
CRITICAL:__main__:Failed main loop: invalid syntax (handlers.py, line 85)
Traceback (most recent call last):
  File "<ipython-input-3-8858cfa24a03>", line 180, in <cell line: 0>
    asyncio.run(main_async(TELEGRAM_BOT_TOKEN, API_ID, API_HASH, TARGET_CHAT_ID))
  File "/usr/local/lib/python3.11/dist-packages/nest_asyncio.py", line 30, in run
    return loop.run_until_complete(task)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.11/dist-packages/nest_asyncio.py", line 98, in run_until_complete
    return f.result()
           ^^^^^^^^^^
  File "/usr/lib/python3.11/asyncio/futures.py", line 203, in result
    raise self._exception.with_traceback(self._exception_tb)
  File "/usr/lib/python3.11/asyncio/tasks.py", line 277, in __step
    result = coro.send(None)
             ^^^^^^^^^^^^^^^
  File "<ipython-input-3-8858cfa24a03>", line 112, in main_async
    try: import handlers
         ^^^^^^^^^^^^^^^
  File "/content/DirectDownload-Tg/handlers.py", line 85
    except Exception as e: logger.warning(f"Failed send/edit cancel confirm: {e}"); try: await context.bot.send_message(update.effective_chat.id, message_text)
                                                                                    ^^^
SyntaxError: invalid syntax
Configuration valid. Starting bot...
Starting main async loop using asyncio.run()...
❌ Critical error: invalid syntax (handlers.py, line 85)

--- Bot execution block finished ---
