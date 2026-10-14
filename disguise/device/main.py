# Boot launcher: always starts the smart-home disguise. The SeedSigner app only
# starts after the secret (6 quick taps on the top-left corner of the Home screen),
# and any exit or crash of SeedSigner resets the board, which boots back here.
import sys
sys.path.insert(0, '/overlay')
import gc

_unlock = False
try:
    import disguise_app
    _unlock = disguise_app.run()
except KeyboardInterrupt:
    print('[disguise] interrupted; dropping to REPL.')
except Exception as e:
    sys.print_exception(e)
    print('[disguise] crashed; dropping to REPL.')

if _unlock:
    # Unload the disguise (and its radio) before SeedSigner takes the screen.
    try:
        disguise_app.shutdown()
    except Exception as e:
        sys.print_exception(e)
    for _m in ('disguise_app', 'switches', 'wifi', 'tuya'):
        sys.modules.pop(_m, None)
    disguise_app = None
    gc.collect()

    import machine
    try:
        from seedsigner.controller import Controller
        Controller.get_instance().start()
    except Exception as e:
        sys.print_exception(e)
    # Whatever happened inside SeedSigner, come back up as the disguise.
    machine.reset()
