import importlib.util, inspect
spec = importlib.util.spec_from_file_location('app', r'E:\deepseek-works\koubo-studio\app.py')
app = importlib.util.module_from_spec(spec); spec.loader.exec_module(app)
head = inspect.getsource(app._ai_menu).split('fam_desc')[0]
for k in ('"style"','"title"','"caption"','"atmo"','"lt"','"trans"','"bgb"','"cta"','"data"','"cmp"','"list"'):
    print(k, '->', k in head)
