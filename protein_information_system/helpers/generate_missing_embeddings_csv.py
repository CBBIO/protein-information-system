import csv
import psycopg2
from protein_information_system.helpers.config.yaml import read_yaml_config

def generate_missing_embeddings_csv():
    # Read configuration
    conf = read_yaml_config('protein_information_system/config/config.yaml')
    
    # Database connection parameters
    db_params = {
        'host': conf['postgres_host'],
        'port': conf['postgres_port'],
        'database': conf['postgres_db'],
        'user': conf['postgres_user'],
        'password': conf['postgres_password']
    }
    
    try:
        # Connect to database
        conn = psycopg2.connect(**db_params)
        cur = conn.cursor()
        
        # SQL query to get embedding status for each protein
        query = """
        SELECT 
            p.id as protein_id,
            MAX(CASE WHEN set.name = 'ESM' AND se.layer_index = 0 THEN 1 ELSE 0 END) as has_esm,
            MAX(CASE WHEN set.name = 'Prot-T5' AND se.layer_index = 0 THEN 1 ELSE 0 END) as has_prott5,
            MAX(CASE WHEN set.name = 'Prost-T5' AND se.layer_index = 0 THEN 1 ELSE 0 END) as has_prostt5,
            MAX(CASE WHEN set.name = 'Ankh3-Large' AND se.layer_index = 0 THEN 1 ELSE 0 END) as has_ankh3_large,
            MAX(CASE WHEN set.name = 'ESM3c' AND se.layer_index = 0 THEN 1 ELSE 0 END) as has_esm3c
        FROM protein p
        LEFT JOIN sequence_embeddings se ON p.sequence_id = se.sequence_id
        LEFT JOIN sequence_embedding_type set ON se.embedding_type_id = set.id
        GROUP BY p.id
        ORDER BY p.id;
        """
        
        cur.execute(query)
        results = cur.fetchall()
        
        # Write to CSV
        with open('missing_embeddings(last_layer).csv', 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['protein_id', 'has_esm', 'has_prott5', 'has_prostt5', 'has_ankh3_large', 'has_esm3c']
            writer = csv.writer(csvfile)
            
            # Write header
            writer.writerow(fieldnames)
            
            # Write data
            for row in results:
                writer.writerow(row)
        
        print(f"✅ CSV generated successfully!")
        print(f"📊 Total proteins processed: {len(results)}")
        
        # Print summary statistics
        if results:
            esm_count = sum(1 for row in results if row[1] == 1)
            prott5_count = sum(1 for row in results if row[2] == 1)
            prostt5_count = sum(1 for row in results if row[3] == 1)
            ankh3_count = sum(1 for row in results if row[4] == 1)
            esm3c_count = sum(1 for row in results if row[5] == 1)
            
            print(f"\n📈 Embedding Statistics:")
            print(f"ESM: {esm_count}/{len(results)} ({esm_count/len(results)*100:.1f}%)")
            print(f"Prot-T5: {prott5_count}/{len(results)} ({prott5_count/len(results)*100:.1f}%)")
            print(f"Prost-T5: {prostt5_count}/{len(results)} ({prostt5_count/len(results)*100:.1f}%)")
            print(f"Ankh3-Large: {ankh3_count}/{len(results)} ({ankh3_count/len(results)*100:.1f}%)")
            print(f"ESM3c: {esm3c_count}/{len(results)} ({esm3c_count/len(results)*100:.1f}%)")
        
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        if 'cur' in locals():
            cur.close()
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    generate_missing_embeddings_csv()