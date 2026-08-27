# Initial 25-stock TASI universe. This is a screening universe, not a buy list.
# Symbols are Saudi Exchange tickers. The engine still validates symbols through SAHMK.
TASI_25 = [
    {"symbol":"1120","name":"مصرف الراجحي","sector":"البنوك"},
    {"symbol":"1180","name":"البنك الأهلي السعودي","sector":"البنوك"},
    {"symbol":"1150","name":"مصرف الإنماء","sector":"البنوك"},
    {"symbol":"1010","name":"بنك الرياض","sector":"البنوك"},
    {"symbol":"1140","name":"مصرف البلاد","sector":"البنوك"},
    {"symbol":"1060","name":"البنك السعودي الأول","sector":"البنوك"},
    {"symbol":"2222","name":"أرامكو السعودية","sector":"الطاقة"},
    {"symbol":"2010","name":"سابك","sector":"المواد الأساسية"},
    {"symbol":"1211","name":"معادن","sector":"المواد الأساسية"},
    {"symbol":"2020","name":"سابك للمغذيات الزراعية","sector":"المواد الأساسية"},
    {"symbol":"2082","name":"أكوا باور","sector":"المرافق"},
    {"symbol":"5110","name":"السعودية للكهرباء","sector":"المرافق"},
    {"symbol":"7010","name":"stc","sector":"الاتصالات"},
    {"symbol":"7020","name":"موبايلي","sector":"الاتصالات"},
    {"symbol":"7202","name":"solutions by stc","sector":"البرمجيات والخدمات"},
    {"symbol":"7203","name":"علم","sector":"البرمجيات والخدمات"},
    {"symbol":"1111","name":"مجموعة تداول السعودية","sector":"الخدمات المالية"},
    {"symbol":"2280","name":"المراعي","sector":"الأغذية"},
    {"symbol":"2050","name":"مجموعة صافولا","sector":"الأغذية"},
    {"symbol":"4164","name":"النهدي الطبية","sector":"الرعاية الصحية"},
    {"symbol":"4190","name":"جرير","sector":"تجزئة"},
    {"symbol":"4003","name":"إكسترا","sector":"تجزئة"},
    {"symbol":"4250","name":"جبل عمر","sector":"إدارة وتطوير العقارات"},
    {"symbol":"4300","name":"دار الأركان","sector":"إدارة وتطوير العقارات"},
    {"symbol":"4004","name":"دله الصحية","sector":"الرعاية الصحية"},
]

SYMBOLS = [x["symbol"] for x in TASI_25]
BY_SYMBOL = {x["symbol"]: x for x in TASI_25}
