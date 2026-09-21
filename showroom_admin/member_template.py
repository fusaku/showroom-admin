"""Defaults derived from the explicitly selected member, without reusing identity."""
from .member_creation import CreationError

SOURCE_ID='shiratori_sari'
SOURCE_NAME='白鳥 沙怜'


def defaults(repo,index):
    if repo.mode=='demo':
        member=dict(member_id=SOURCE_ID,name_jp=SOURCE_NAME,name_en='Shiratori Sari',group_name='AKB48',team='AKB48 19期生',enabled=0,
                    youtube=dict(title_template='',description_template='【チャンネル情報】\nAKB48 橋本陽菜さんのアーカイブは、\n専用チャンネルにて全て公開中です！\n\n▼ 専用チャンネルはこちら\nhttps://www.youtube.com/@harupyon_SR\nぜひチェックしてください！\n\n白鳥 沙怜\nコメント付き:https://www.kg46.com\nアップロード時間:{upload_time}\n\n#AKB48 #白鳥沙怜 #Showroom',category_id='22',privacy_status='public',playlist_id='',use_primary_account=1),tags=['AKB48','19期生','白鳥沙怜','Showroom'])
        match=dict(filename='akb48.jdex',entry=dict(jpnTeam='19期研究生',engTeam='19th',priority=20))
    else:
        member=repo.detail(SOURCE_ID)
        if not member:raise CreationError('默认模板成员不存在，请检查白鳥沙怜的资料。',404)
        match=index.locate(str(member['room_id']))
    y=dict(member.get('youtube') or {});y={k:(v if v is not None else '') for k,v in y.items()}
    source_playlist=y.get('playlist_id','')
    y.update(playlist_id='',tags=list(member.get('tags') or []))
    return dict(source_name=member['name_jp'],source_id=SOURCE_ID,source_english_name=member['name_en'],source_playlist_id=source_playlist,
                member=dict(member_id='',name_jp='',name_en='',room_id='',room_url_key='',group_name=member['group_name'],team=member['team'],enabled=member['enabled'],
                            index_file=match['filename'],index_team=match['entry'].get('jpnTeam',''),eng_team=match['entry'].get('engTeam',''),priority=match['entry'].get('priority',20),youtube=y))
