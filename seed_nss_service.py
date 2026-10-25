"""Create the NSS application service once, preserving subsequent admin edits."""
from datetime import datetime
from db import db
from form_services import parse_fields

def seed():
    result=db.services.update_one({'slug':'nss-special-postings'}, {'$setOnInsert': {
        'slug':'nss-special-postings','name':'NSS Special Postings','purchase_mode':'form',
        'type':'OFF','status':'OPEN','availability':'AVAILABLE','price':None,
        'image_url':'https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcTpw2qcUiSA2UA2_uYjaFgkjNNZxFqIDRiFwFqScMpQdQ&s=10',
        'form_fields':parse_fields(['Name','NSS Number','Company of Choice','District of the Company','Region of the Company']),
        'offers':[],'store_offers':[],'created_at':datetime.utcnow(),'updated_at':datetime.utcnow()
    }},upsert=True)
    print('NSS Special Postings created.' if result.upserted_id else 'NSS Special Postings already exists; existing settings preserved.')
    saved=db.services.find_one({'slug':'nss-special-postings'})
    print('Database:',db.name,'Service ID:',str(saved['_id']),'Price:',saved.get('price'),'Fields:',len(saved.get('form_fields',[])))
if __name__=='__main__':seed()
