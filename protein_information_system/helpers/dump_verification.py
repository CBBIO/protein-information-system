import psycopg2
from protein_information_system.helpers.config.yaml import read_yaml_config

def verify_dump_integrity():
    """Verify data integrity between original and test databases"""
    
    print("🔍 Verifying dump integrity between databases...")
    print("=" * 60)
    
    # Original database connection (using the old format from main config)
    original_conf = read_yaml_config('config/config.yaml')
    
    # Test database connection (using the old format from test config)
    test_conf = read_yaml_config('config/config_test.yaml')
    
    print(f"📊 Original: {original_conf['DB_NAME']}")
    print(f"🧪 Test: {test_conf['DB_NAME']}")
    
    # Verification queries (fixed for halfvec compatibility)
    verification_queries = [
        # Basic row counts
        ("Row Counts", "SELECT COUNT(*) FROM {table}"),
        
        # Sequence embeddings specific checks (fixed for halfvec)
        ("Embedding Stats", """
            SELECT embedding_type_id, COUNT(*), 
                   MIN(array_length(shape, 1)), 
                   MAX(array_length(shape, 1))
            FROM sequence_embeddings 
            GROUP BY embedding_type_id 
            ORDER BY embedding_type_id
        """),
        
        # Sample data checksums for tables with numeric IDs
        ("Sample IDs", "SELECT MIN(id), MAX(id), COUNT(DISTINCT id) FROM {table} WHERE id IS NOT NULL"),
        
        # String-based IDs for some tables
        ("String ID Stats", "SELECT COUNT(DISTINCT {id_column}) FROM {table}"),
    ]
    
    tables_with_numeric_ids = [
        'sequence', 'sequence_embeddings', 'chain', 'protein_go_term_annotation'
    ]
    
    tables_with_string_ids = [
        ('accession', 'code'),
        ('protein', 'id'), 
        ('structure', 'id'),
        ('go_terms', 'go_id')
    ]
    
    all_passed = True
    
    try:
        original_conn = psycopg2.connect(
            host=original_conf['DB_HOST'],
            port=original_conf['DB_PORT'],
            database=original_conf['DB_NAME'],
            user=original_conf['DB_USERNAME'],
            password=original_conf['DB_PASSWORD']
        )
        
        test_conn = psycopg2.connect(
            host=test_conf['DB_HOST'],
            port=test_conf['DB_PORT'],
            database=test_conf['DB_NAME'],
            user=test_conf['DB_USERNAME'],
            password=test_conf['DB_PASSWORD']
        )
        
        # Set autocommit to avoid transaction issues
        original_conn.autocommit = True
        test_conn.autocommit = True
        
        # Check row counts for all tables
        print(f"\n📊 Row Counts:")
        print("-" * 40)
        all_tables = tables_with_numeric_ids + [table for table, _ in tables_with_string_ids]
        for table in all_tables:
            query = "SELECT COUNT(*) FROM {}".format(table)
            if not compare_query_results(original_conn, test_conn, query, f"{table}"):
                all_passed = False
        
        # Check embedding stats
        print(f"\n📊 Embedding Stats:")
        print("-" * 40)
        embedding_query = """
            SELECT embedding_type_id, COUNT(*), 
                   MIN(array_length(shape, 1)), 
                   MAX(array_length(shape, 1))
            FROM sequence_embeddings 
            GROUP BY embedding_type_id 
            ORDER BY embedding_type_id
        """
        if not compare_query_results(original_conn, test_conn, embedding_query, "Embedding Stats"):
            all_passed = False
        
        # Check numeric ID ranges
        print(f"\n📊 Numeric ID Ranges:")
        print("-" * 40)
        for table in tables_with_numeric_ids:
            query = "SELECT MIN(id), MAX(id), COUNT(DISTINCT id) FROM {} WHERE id IS NOT NULL".format(table)
            if not compare_query_results(original_conn, test_conn, query, f"{table} IDs"):
                all_passed = False
        
        # Check string ID counts
        print(f"\n📊 String ID Counts:")
        print("-" * 40)
        for table, id_column in tables_with_string_ids:
            query = "SELECT COUNT(DISTINCT {}) FROM {}".format(id_column, table)
            if not compare_query_results(original_conn, test_conn, query, f"{table} {id_column}s"):
                all_passed = False
        
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return False
    
    finally:
        if 'original_conn' in locals():
            original_conn.close()
        if 'test_conn' in locals():
            test_conn.close()
    
    if all_passed:
        print("\n✅ All verification checks passed! Dump integrity confirmed.")
    else:
        print("\n❌ Some verification checks failed! Review the differences above.")
    
    return all_passed


def compare_query_results(conn1, conn2, query, description):
    """Compare results of the same query on two databases"""
    try:
        cur1 = conn1.cursor()
        cur2 = conn2.cursor()
        
        cur1.execute(query)
        result1 = cur1.fetchall()
        
        cur2.execute(query)
        result2 = cur2.fetchall()
        
        if result1 == result2:
            print(f"  ✅ {description}: MATCH")
            return True
        else:
            print(f"  ❌ {description}: MISMATCH")
            print(f"     Original: {result1}")
            print(f"     Test: {result2}")
            return False
            
    except Exception as e:
        print(f"  ⚠️  {description}: ERROR - {e}")
        return False
    finally:
        if 'cur1' in locals():
            cur1.close()
        if 'cur2' in locals():
            cur2.close()