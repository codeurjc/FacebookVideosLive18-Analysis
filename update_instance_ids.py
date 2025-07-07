import os
import pandas as pd
import glob

def main():
    ie_dir = os.path.join(os.path.dirname(__file__), 'instance-equivalents')
    ae_dir = os.path.join(os.path.dirname(__file__), 'alt_exp_results')

    for mapping_filename in os.listdir(ie_dir):
        if not mapping_filename.endswith('.csv'):
            continue
        base = mapping_filename[:-4]
        mapping_path = os.path.join(ie_dir, mapping_filename)
        mapping_df = pd.read_csv(mapping_path)
        mapping_df['file_num'] = (
            mapping_df['instance']
            .str.rsplit('-', n=1)
            .str[-1]
            .str.replace('.csv', '', regex=False)
            .astype(int)
        )
        num_to_id = mapping_df.set_index('file_num')['instanceID'].to_dict()

        pattern = os.path.join(ae_dir, f"{base}-*.csv")
        for target_path in glob.glob(pattern):
            target_df = pd.read_csv(target_path)
            # Map 0–29 using instanceID, and map 30+ to '1t', '2t', ...
            orig = target_df['instance'].astype(int)
            target_df['instance'] = orig.apply(
                lambda x: f"{x-29}t" if x >= 30 else num_to_id.get(x)
            )
            fname = os.path.basename(target_path)
            output_path = os.path.join(ae_dir, fname)
            target_df.to_csv(output_path, index=False)
            print(f"Updated {target_path} -> {output_path}")

if __name__ == '__main__':
    main()
