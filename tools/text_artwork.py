"""Source-only inventory of visually identified text textures."""
# Page numbers are zero-based. Empty transcriptions await Japanese source review.
PAGES = {
    "MEMO1": {
        1: ("case notes", """光定　夕食後、自室で三村といっしょ。
マユラ　自室。真奈美と三村の口論の時２階にいた。
ヨネ　厨房、食堂であと片づけ。
真奈美　屋敷の掃除など。その後、光定の部屋の前で三村と口論。その後、いつきといっしょに時計の操作盤へ。

三村　自室。遠藤といっしょ。その後、光定の部屋。
遠藤　三村の部屋。その後、食堂から自室へ。
小百合　食堂。その後、自室。その後、いつきを捜す。その後、食堂。俺と美雪もいっしょ。
灯妙　食堂。１５分くらいトイレに立つ"""),
        2: ("case notes", """２日目
１０月１５日
被害者　葉月光定
見たて　人形の舞
殺害方法　間欠泉へ突き落とす
状況
光定は睡眠薬で眠らされ、車椅子で連れ出された。
９時にワルキューレが鳴った。
光定が部屋にいると思わせるため？
犯行時刻
光定は夕食後（８時頃）自室へ。
真奈美以外、光定の部屋に出入りした者はいない。
犯人が光定を連れ出したのは、８時３０分からワルキューレ（９時）までの間だと思われる。
死体発見は１０時。場所は間欠泉。"""),
        3: ("case notes", """マユラ　８時３０分～９時、入浴。美雪と三村といっしょ。１０時、間欠泉へ。
ヨネ　厨房、食堂であと片づけ。
真奈美　光定の部屋　睡眠薬で眠らされた（本人談）
三村　８時３０分～９時、入浴。美雪、マユラといっしょ。その後、自室へ。

遠藤　自室。
灯妙　大広間。（寝てた）
小百合　自室。その後、光定の部屋へ時計を拾いに行く（ワルキューレより前）"""),
        4: ("case notes", """３日目
１０月１６日
被害者　ニセマサキ（佐藤治）
見たて　骸の鉾
殺害方法　ナイフで心臓をひと突き
状況
死体が乗ったボートが入り江で発見された。
朝１０時、ニセマサキは三村、遠藤と口論。その後、屋敷を飛び出して行方不明になっていた。
マユラがボートを発見したのが夕方６時前（本人談）"""),
        5: ("case notes", """マユラ　１１時３０分、俺と美雪といっしょに仮宮へ。夕方、入り江へ。
真奈美　夕方、小百合と揉みあい、崖から落ちて気を失い、医務室へ。
三村　昼すぎまでニセマサキ捜し（本人談）その後、自室。２時頃、いつきと会った。夕方、真奈美の手当。

遠藤　自室。
灯妙　島のあちこちで宝探し。夕方５時３０分、入り江で立ち小便。
小百合　１０時すぎ俺とボート。その後、自室。夕方、真奈美と揉みあい。
ヨネ　屋敷内で家事　外へは出ていない。（本人談）"""),
        0: ("case notes", "1日目\n10月14日\n被害者 木暮条一郎\n見たて 朱き手毬\n"
            "殺害方法 不明\n殺害後、時計塔の小窓から死体の首を出し、大時計の針で切断した。\n"
            "犯行時刻 夕食後から、死体発見までの間。\n"
            "状況\n大時計は、3日に一度、真奈美がスイッチを入れることになっている。\n"
            "操作盤は、玄関を出た所にある。\n"
            "鐘のスイッチは、ふだんは切ってあるが当夜は鐘が鳴った。\n"
            "時計塔の扉は閉まっており、1本しかない鍵は、死体のポケットの中。密室？"),
        6: ("deduction verse and cipher", "壱の譜（白の鳥居）\n天照らす星\n満つる時\n荒ぶる神\n夜に闇を誘い\n熱き柱天に届き\n大地に星\n現れん"),
        7: ("deduction verse and cipher", "弐の譜（緑の鳥居）\n猛き獣人の童\n星を守りて\nあまた在り\n童互いに\n相まみえず\n糸結びて\n星導かん。"),
        8: ("deduction verse and cipher", "参の譜（黒の鳥居）\n童　黙して\n語らず\nされど集いて\n戯る時\n語ることなき童\n星の行方示さん"),
        10: ("save and load interface",
             "ロードしました。 セーブしました。\nセーブしますか？ セーブ ロード\n"
             "はい いいえ\nカード1 カード2\nロード中\nセーブ中\nメモリーカードチェック中"),
    },
    "SIMA": {1: ("island map labels", ""), 2: ("island destination labels", "")},
    "YASIKI1": {0: ("mansion floor plan", "木暮　遠藤　マユラ　ヨネ　いつき　剣持\n葉月邸 1階"), 1: ("mansion floor plan", "小百合　クリス　マユキ　真奈美　美雪　金田一　光定\n3階（時計塔）")},
    "YASIKI2": {0: ("mansion floor plan variant", ""), 1: ("mansion floor plan variant", "")},
    "OPN": {page: ("title and copyright artwork", "") for page in range(3)},
    "STAFF": {**{page: ("credits", "") for page in range(12)}, 16: ("ending card", "完")},
}


def artwork_entries(resource, data, make_entry):
    from graphics import parse

    path = resource["path"]
    pages, _ = parse(data, ".TXP")
    for number, (category, jp) in PAGES[path.split("/")[-1][:-4]].items():
        offset, x, y = pages[number]
        yield make_entry(resource, offset, data[offset:offset + 32768], jp, "artwork",
                         encoding="indexed texture", page=number, category=category,
                         vram=[x, y], transcription="partial" if jp else "pending",
                         provenance="Visual inspection of decoded texture; runtime crop and CLUT unverified")
