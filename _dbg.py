import io
with io.open(r'C:\Users\陈光龙\WorkBuddy\数据库同步\dbcore.py', 'r', encoding='utf-8') as f:
    t = f.read()
i = t.find('elif dialect == "mysql":\n        if (src.table_comment')
print(t[i-50:i+900])
